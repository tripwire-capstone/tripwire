"""Metric D, naming (phase 2; frozen with the rest; only with `score run --naming`).

Replays name clusters with placeholders (the old no-LLM step), so `name_replay_clusters` runs inside
sweep.replay's `before_drop` hook: it clears those placeholder names and names every replayed
cluster with the production namer (llm_naming.name_clusters: same sampling, prompt and model),
oldest cluster first, as new clusters are named in production. Then it reads what D needs.

  D1 keyword_check   park only, no LLM.
  D2 rubric_grade    a different, stronger model grades each name (placeholder model and rubric
                     until the user approves them: off unless a grader is passed; tests use a fake).
  D3 laminar_match   cosine similarity of the two names' embeddings, for mapping-table pairs with
                     >= 3 shared traces.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tripwire.clusterer import llm_naming
from tripwire.llm import LLMError

from . import PACKAGE_DIR

KEYWORDS = {
    "large_party": r"party|group|size|capacity|site count|people",
    "decoy": r"sold out|unavailab|availability|fully booked",
    "glacier_mixup": r"glacier|wrong park|park mix|misidentif",
    "timeout": r"timeout|timed out|time out|lookup fail|failed lookup",
    "date_shift": r"date",
}
MAJORITY = 0.5
CENTRAL_MEMBERS = 10
FAR_MEMBERS = 3
MIN_SHARED_FOR_NAME_MATCH = 3
GRADE_PROMPT = PACKAGE_DIR / "prompts" / "grade_v1.md"
GRADE_SCHEMA = PACKAGE_DIR / "prompts" / "grade_v1.schema.json"
GRADE_CRITERIA = ("cause", "fit", "distinct")


class CostCounter:
    """Wraps an LLM client: counts calls and adds up cost_usd from each result's usage."""

    def __init__(self, llm):
        self.llm, self.calls, self.cost_usd = llm, 0, 0.0

    def complete(self, model, messages, json_schema):
        self.calls += 1
        try:
            result = self.llm.complete(model, messages, json_schema)
        except LLMError as error:
            self.cost_usd += getattr(getattr(error, "usage", None), "cost_usd", None) or 0.0
            raise
        self.cost_usd += result.usage.cost_usd or 0.0
        return result


@dataclass
class NamedCluster:
    cluster_id: int
    signal_id: int
    signal_name: str
    name: str
    source: str                 # 'llm' or 'fallback' (the namer's own fallback on a failed call)
    central: list               # summaries of the 10 members closest to the centroid
    far: list                   # summaries of the 3 farthest
    siblings: list              # names of the signal's other replayed clusters


CLEAR_PLACEHOLDERS_SQL = """
UPDATE clusters SET name = NULL, name_source = NULL, named_at = NULL, named_member_count = NULL
"""

CLUSTERS_SQL = """
SELECT cluster.id, cluster.signal_id, signal.name, cluster.name, cluster.name_source
FROM clusters cluster JOIN signals signal ON signal.id = cluster.signal_id
WHERE cluster.level = 0 AND cluster.name IS NOT NULL
ORDER BY cluster.id
"""

MEMBER_SUMMARIES_SQL = """
SELECT summary.summary
FROM cluster_members member
JOIN event_embeddings embedded ON embedded.event_id = member.event_id
JOIN clusters cluster ON cluster.id = member.cluster_id
JOIN event_summaries summary ON summary.event_id = member.event_id AND summary.status = 'done'
WHERE member.cluster_id = %s
ORDER BY embedded.embedding <=> cluster.centroid, member.event_id
"""


def name_replay_clusters(conn, llm, model):
    """Inside the replay copy: replace placeholder names with the real namer's. Returns
    {cluster_id: NamedCluster}. One paid call per cluster (with a real llm)."""
    conn.execute(CLEAR_PLACEHOLDERS_SQL)
    prompt = llm_naming.load_naming_prompt()
    # max_renames=0: every cluster is "new" (unnamed), named in id order with its final members.
    result = llm_naming.name_clusters(conn, llm, model, prompt, growth=2.0, max_renames=0)
    if result.service_down:
        raise RuntimeError("naming: OpenAI unavailable during --naming; names would be fallbacks")
    rows = conn.execute(CLUSTERS_SQL).fetchall()
    names_by_signal = {}
    for cluster_id, signal_id, _, name, _ in rows:
        names_by_signal.setdefault(signal_id, []).append((cluster_id, name))
    named = {}
    for cluster_id, signal_id, signal_name, name, source in rows:
        summaries = [summary for (summary,) in conn.execute(MEMBER_SUMMARIES_SQL, (cluster_id,))]
        central = summaries[:CENTRAL_MEMBERS]
        far = summaries[CENTRAL_MEMBERS:][-FAR_MEMBERS:]
        siblings = [other for other_id, other in names_by_signal[signal_id] if other_id != cluster_id]
        named[cluster_id] = NamedCluster(cluster_id, signal_id, signal_name, name, source or "", central,
                                         far, siblings)
    return named


# ------------------------------------------------------------------ D1

def majority_cause(events, labels_by_trace):
    """The planted cause held by >= 50% of the cluster's labeled events (labels can hold several
    causes with `sofar`; the highest share wins, ties to the lowest name), or None."""
    labeled = [labels_by_trace.get(event.trace_id, frozenset()) for event in events]
    labeled = [labels for labels in labeled if labels]
    if not labeled:
        return None, 0.0
    counts = Counter(cause for labels in labeled for cause in labels)
    best = max(counts.values())
    cause = min(cause for cause, count in counts.items() if count == best)
    share = best / len(labeled)
    return (cause, share) if share >= MAJORITY else (None, share)


def keyword_check(events_by_cluster, names, labels_by_trace):
    """D1 for one signal: {score, n_clusters, rows}. score = matching / clusters with a majority
    cause that has a keyword list; None when no cluster qualifies."""
    rows = []
    for cluster_id in sorted(events_by_cluster, key=str):
        cause, share = majority_cause(events_by_cluster[cluster_id], labels_by_trace)
        if cause not in KEYWORDS:
            continue
        name = names.get(cluster_id) or ""
        rows.append({"cluster": cluster_id, "name": name, "cause": cause, "share": share,
                     "match": bool(re.search(KEYWORDS[cause], name, re.IGNORECASE))})
    matched = sum(1 for row in rows if row["match"])
    return {"score": matched / len(rows) if rows else None, "n_clusters": len(rows), "rows": rows}


# ------------------------------------------------------------------ D2

def grade_messages(cluster):
    system = GRADE_PROMPT.read_text().strip()
    lines = [f"Signal: {cluster.signal_name}", f"Cluster name: {cluster.name}", "",
             "Members closest to the cluster's center:"]
    lines += [f"- {summary}" for summary in cluster.central]
    lines += ["", "Members farthest from the center:"]
    lines += [f"- {summary}" for summary in cluster.far] or ["- (none: the cluster is small)"]
    lines += ["", "Names of the other clusters of this signal:"]
    lines += [f"- {name}" for name in cluster.siblings] or ["- (none)"]
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(lines)}]


def rubric_grade(clusters, grader, model):
    """D2: per cluster, three 0/1/2 criteria; cluster score = sum / 6; score = mean."""
    schema = json.loads(GRADE_SCHEMA.read_text())
    rows = []
    for cluster in clusters:
        output = grader.complete(model, grade_messages(cluster), schema).output
        values = [output.get(criterion) for criterion in GRADE_CRITERIA]
        if not all(value in (0, 1, 2) for value in values):
            raise ValueError(f"grader returned {output!r} for cluster {cluster.cluster_id}")
        rows.append({"cluster": cluster.cluster_id, "name": cluster.name,
                     **dict(zip(GRADE_CRITERIA, values)), "score": sum(values) / 6})
    return {"score": sum(row["score"] for row in rows) / len(rows) if rows else None,
            "n_clusters": len(rows), "model": model, "rows": rows}


# ------------------------------------------------------------------ D3

def cosine(first, second):
    first, second = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    return float(first @ second / (np.linalg.norm(first) * np.linalg.norm(second)))


def laminar_match(mapping, tripwire_names, laminar_names, embedder):
    """D3: mapping-table pairs with >= 3 shared traces (Tripwire side clustered)."""
    pairs = [(laminar_names.get(row["laminar_cluster"], ""), tripwire_names.get(row["tripwire_cluster"], ""))
             for row in mapping
             if row["n_shared"] >= MIN_SHARED_FOR_NAME_MATCH and row["tripwire_cluster"] != "unclustered"]
    pairs = [(laminar, tripwire) for laminar, tripwire in pairs if laminar and tripwire]
    if not pairs:
        return {"score": None, "n_pairs": 0, "rows": []}
    vectors = embedder.embed([text for pair in pairs for text in pair])
    rows = [{"laminar": laminar, "tripwire": tripwire, "cosine": cosine(vectors[2 * index], vectors[2 * index + 1])}
            for index, (laminar, tripwire) in enumerate(pairs)]
    return {"score": sum(row["cosine"] for row in rows) / len(rows), "n_pairs": len(rows), "rows": rows}


def score_naming(tripwire_view, named, answer_keys, agreement_by_signal, laminar_view, grader, grader_model,
                 embedder, label_modes):
    """All of D per signal. `named`: name_replay_clusters' result."""
    names = {cluster_id: cluster.name for cluster_id, cluster in named.items()}
    out = {}
    for key, events in tripwire_view.events.items():
        by_cluster = {}
        for event in events:
            if event.single is not None:
                by_cluster.setdefault(event.single, []).append(event)
        entry = {"n_named": len(by_cluster),
                 "n_fallback": sum(1 for cluster_id in by_cluster if named.get(cluster_id) and
                                   named[cluster_id].source == "fallback")}
        mode = label_modes.get(key)
        entry["keyword"] = (keyword_check(by_cluster, names, answer_keys[mode])
                            if answer_keys is not None and mode else None)
        entry["grade"] = (rubric_grade([named[cluster_id] for cluster_id in sorted(by_cluster) if cluster_id in named],
                                       grader, grader_model) if grader is not None else None)
        agreement = agreement_by_signal.get(key)
        entry["laminar_match"] = (laminar_match(agreement["mapping"], names, laminar_view.cluster_names, embedder)
                                  if agreement and laminar_view is not None and embedder is not None else None)
        out[key] = entry
    return out
