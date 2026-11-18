"""Scoring one run: replay Tripwire's clustering with the working-tree code, then compute A-D.

Replay settings (decided Nov 18 with the user): the clusterer's own defaults from the working
tree's settings.py, NOT the shell's environment (a stray CLUSTER_* variable would change scores
silently), plus:
  * `threshold` for CLUSTER_ASSIGN_THRESHOLD (sweep.replay's own argument), and
  * CLUSTER_POOL_WINDOW_DAYS=36500: the pool window is measured from now(), so after ~14 days the
    saved runs could no longer form clusters in a replay and scores would drift with the date.
The settings used are recorded in scores.json.
"""

import os
from dataclasses import asdict

from tripwire.clusterer import resummarize, sweep
from tripwire.clusterer.report import load_answer_key
from tripwire.clusterer.settings import DEFAULTS

from . import data, metrics, naming
from .database import connect

REPLAY_ENVIRON = {"CLUSTER_POOL_WINDOW_DAYS": "36500"}
NAMES_SQL = "SELECT id, name FROM clusters"


def default_threshold():
    return DEFAULTS["CLUSTER_ASSIGN_THRESHOLD"][0]


def replay_settings(threshold):
    settings = asdict(sweep.replay_settings(threshold, REPLAY_ENVIRON))
    settings.pop("database_url", None)
    return settings


def load_answer_keys(run):
    if run.kind != "park":
        return None
    return {mode: {str(trace): labels for trace, labels in load_answer_key([run.answer_key_file], mode).items()}
            for mode in ("turn", "sofar")}


def check_answer_key_matches_raw(run, raw):
    """raw.json carries a copy of the answer key; it should be the same conversations."""
    if run.kind != "park" or not raw.get("answer_key"):
        return None
    import json
    file_convos = {json.loads(line)["convo_id"] for line in run.answer_key_file.read_text().splitlines()
                   if line.strip()}
    raw_convos = {row["convo_id"] for row in raw["answer_key"]}
    return {"identical": file_convos == raw_convos, "n_file": len(file_convos), "n_raw": len(raw_convos)}


class RetryingEmbedder:
    """Retries an embedding batch on a service error (OpenAI 5xx, timeout, rate limit). Added Nov
    18 after a single 500 from the embeddings API threw away a run's paid re-summaries (the
    --as-version schema is dropped on any error). Other errors raise at once."""

    def __init__(self, embedder, tries=3, wait_seconds=5.0, sleep=None):
        import time
        self.embedder, self.tries, self.wait_seconds = embedder, tries, wait_seconds
        self.sleep = sleep or time.sleep
        self.model = embedder.model

    def embed(self, texts):
        from tripwire.llm import LLMServiceError
        for attempt in range(1, self.tries + 1):
            try:
                return self.embedder.embed(texts)
            except LLMServiceError:
                if attempt == self.tries:
                    raise
                self.sleep(self.wait_seconds * attempt)


class Paid:
    """The paid clients for --as-version and --naming, built only when needed (tests pass fakes)."""

    def __init__(self, llm=None, embedder=None, namer_model=None, summarizer_model=None, grader=None,
                 grader_model=None):
        self.llm, self.embedder, self.grader, self.grader_model = llm, embedder, grader, grader_model
        self.namer_model = namer_model or DEFAULTS["NAMING_MODEL"][0]
        self.summarizer_model = summarizer_model or DEFAULTS["SUMMARIZER_MODEL"][0]

    @classmethod
    def real(cls, grader_model=None):
        from dotenv import load_dotenv

        from tripwire.clusterer.embed import OpenAIEmbedder
        from tripwire.clusterer.llm_client import OpenAIClient

        from . import BACKEND_DIR
        load_dotenv(BACKEND_DIR / ".env")
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise SystemExit("this needs OPENAI_API_KEY (signals/backend/.env): it makes paid calls")
        client = OpenAIClient(key)
        return cls(llm=client, embedder=RetryingEmbedder(OpenAIEmbedder(key)),
                   grader=client if grader_model else None, grader_model=grader_model)


def replay_run(conn, threshold, source_schema=None, name_with=None):
    """sweep.replay plus the replayed clusters' names. `name_with` = (llm, model) to name them
    with the production namer first (--naming). Returns (membership, names, named or None)."""
    captured = {}

    def before_drop(copy_conn):
        if name_with is not None:
            captured["named"] = naming.name_replay_clusters(copy_conn, *name_with)
        captured["names"] = dict(copy_conn.execute(NAMES_SQL))

    membership = sweep.replay(conn, threshold, source_schema=source_schema, environ=REPLAY_ENVIRON,
                              before_drop=before_drop)
    return membership, captured["names"], captured.get("named")


def score_run(run, threshold, as_version=None, signals=None, with_naming=False, paid=None, log=print,
              live=True):
    """Everything for one run. Returns a JSON-able dict. `live=False` skips the raw.json side
    (no live numbers, no Laminar): only for unit tests of the replay path."""
    signal_keys = [data.signal_key(name) for name in signals] if signals else None
    answer_keys = load_answer_keys(run)
    raw = data.load_raw(run.laminar_raw) if live else None
    laminar = data.laminar_from_raw(raw) if raw else None
    result = {"run": run.id, "kind": run.kind, "set": run.set, "db": run.db, "threshold": threshold,
              "as_version": as_version, "signals_filter": signals, "settings": replay_settings(threshold),
              "notes": run.notes, "costs": {}}

    with connect(run.db) as conn:
        stored = data.tripwire_from_db(conn)
        if raw is not None:
            live_tripwire = data.tripwire_from_raw(raw)
            result["stored_vs_raw"] = data.compare_stored_to_raw(stored, live_tripwire)
            result["answer_key_vs_raw"] = check_answer_key_matches_raw(run, raw)
            result["live"] = metrics.score_signals(live_tripwire, laminar, answer_keys, signal_keys)
            for key, entry in result["live"].items():
                _attach_names(entry, live_tripwire.cluster_names, laminar.cluster_names)

        paid_llm = naming.CostCounter(paid.llm) if (paid and paid.llm) else None
        name_with = (paid_llm, paid.namer_model) if with_naming else None
        if with_naming and paid_llm is None:
            raise ValueError("--naming needs an LLM client")

        if as_version is None:
            log(f"{run.id}: replaying at threshold {threshold} ...")
            membership, names, named = replay_run(conn, threshold, name_with=name_with)
        else:
            membership, names, named = _replay_as_version(conn, run, threshold, as_version, signal_keys,
                                                          stored, paid, name_with, result, log)
        replayed = data.tripwire_from_db(conn, membership=membership, names=names)

    result["replay"] = metrics.score_signals(replayed, laminar, answer_keys, signal_keys)
    for key, entry in result["replay"].items():
        _attach_names(entry, replayed.cluster_names, laminar.cluster_names if laminar else {})

    if with_naming:
        agreement_by_signal = {key: entry.get("agreement") for key, entry in result["replay"].items()}
        grader = naming.CostCounter(paid.grader) if paid.grader is not None else None
        naming_view = replayed if signal_keys is None else _only(replayed, signal_keys)
        result["naming"] = naming.score_naming(naming_view, named, answer_keys, agreement_by_signal, laminar,
                                               grader, paid.grader_model, paid.embedder,
                                               metrics.LABEL_MODE_BY_SIGNAL)
        result["costs"]["namer_calls"] = paid_llm.calls
        result["costs"]["namer_usd"] = round(paid_llm.cost_usd, 4)
        if grader is not None:
            result["costs"]["grader_calls"], result["costs"]["grader_usd"] = grader.calls, round(grader.cost_usd, 4)
    return result


def _only(view, keys):
    subset = data.SystemView(view.system, cluster_names=view.cluster_names)
    subset.signals = {key: view.signals[key] for key in keys if key in view.signals}
    subset.events = {key: view.events[key] for key in keys if key in view.events}
    return subset


def _replay_as_version(conn, run, threshold, as_version, signal_keys, stored, paid, name_with, result, log):
    """--as-version: re-summarize (paid) into an asver_ schema, replay that, drop it."""
    from tripwire.clusterer.summarize import load_prompt

    if paid is None or paid.llm is None or paid.embedder is None:
        raise ValueError("--as-version needs an LLM client and an embedder")
    keys = signal_keys or sorted(stored.signals)
    event_ids = [event.event_id for key in keys for event in stored.events.get(key, [])]
    resummarize.drop_leftover_schemas(conn)
    sweep.drop_leftover_schemas(conn)
    log(f"{run.id}: re-summarizing {len(event_ids)} events under prompt v{as_version} "
        f"({paid.summarizer_model}) ...")
    schema, updates = resummarize.build_version_schema(conn, event_ids, paid.llm, paid.embedder,
                                                       paid.summarizer_model, load_prompt(as_version))
    try:
        not_done = sorted(event_id for event_id, update in updates.items() if update.status != "done")
        result["costs"]["summarizer_calls"] = len(updates)
        result["costs"]["summarizer_usd"] = round(sum(update.cost_usd or 0.0 for update in updates.values()), 4)
        result["as_version_not_summarized"] = not_done
        log(f"{run.id}: replaying the v{as_version} summaries at threshold {threshold} ...")
        return replay_run(conn, threshold, source_schema=schema, name_with=name_with)
    finally:
        resummarize.drop_schema(conn, schema)


def _attach_names(entry, tripwire_names, laminar_names):
    """Mapping rows get both clusters' names (run outputs only; the gate never shows them)."""
    agreement = entry.get("agreement")
    if not agreement:
        return
    for row in agreement["mapping"]:
        row["laminar_name"] = laminar_names.get(row["laminar_cluster"], "")
        tripwire = row["tripwire_cluster"]
        row["tripwire_name"] = "unclustered" if tripwire == metrics.UNCLUSTERED else (tripwire_names.get(tripwire) or "")
