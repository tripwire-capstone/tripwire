"""Try LLM cluster names side by side on real clusters (SIG-65). A developer tool.

    python -m tripwire.clusterer.try_naming --database-url URL
        [--models gpt-6-luna,gpt-6.1-sol,gpt-6-astra] [--as-of N | --rename-from N]
        [--signal NAME] [--no-laminar] [--workers 4] [--out DIR]

For every leaf cluster with a summarized member, asks each model for a name with the real
naming prompt (llm_naming.py: same input, same prompt, same cleanup), and writes DIR/report.md
and DIR/results.json: per cluster, today's name next to each model's name and the name Laminar
gave the same traces.

Every cluster is named fresh, as a new cluster: no current name is shown to the model, so
models are compared on the same input. `--as-of N` names each cluster from only its first N
members (by when they joined): what it would have been named when it was that small, to check
whether names go stale as clusters grow. (Its center is today's, not the one it had then.)

`--rename-from N` checks renames the way the clusterer does them: each cluster is named from its
first N members, then renamed at its full size with that name shown as the current LLM name
(prompt: keep it unless it no longer fits), and also named fresh at full size for comparison.
Only clusters with at least 2N members, the size at which the clusterer would rename them. The
report says, per model, how often the rename kept the small-size name: rewording a name that
still fits is churn; keeping one that's clearly narrower than the fresh name is anchoring.

Laminar's name: the most specific named cluster (`cluster_details`, lowest level) that most of
this cluster's traces got from the Laminar Signal with the same name (ignoring case), with the
count, e.g. "Order Cancellation Requests (7 of 9 traces)". Read with the backend's own Laminar
settings (LMNR_PROJECT_API_KEY, LMNR_API_URL). Laminar takes about an hour after the last trace
to finish naming; run this after that.

Reads the database only; never writes to it. `--database-url` must be given on the command line
(never taken from DATABASE_URL), like report.py and try_prompts.py. Needs OPENAI_API_KEY.
"""

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from ..llm import LLMError
from .llm_naming import Job, ask_llm, load_input, load_naming_prompt
from .sql import leaf_cluster

DEFAULT_MODELS = ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra"]

# USD per 1M tokens (input, output), standard rates, no caching: for this report only. Checked
# Nov 16 2026: gpt-6-luna from llm_client.PRICES; gpt-6.1-sol and gpt-6-astra from OpenAI's
# model pages as listed by llmreference.com. A model not listed shows tokens, not cost.
PRICES = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-sol": (2.00, 10.00),
    "gpt-6-astra": (10.00, 50.00),
}

CLUSTERS_SQL = f"""
SELECT cluster.id, cluster.signal_id, signal.name, cluster.name, count(*) AS members
FROM clusters cluster
JOIN signals signal ON signal.id = cluster.signal_id
JOIN cluster_members member ON member.cluster_id = cluster.id
WHERE {leaf_cluster("cluster")} AND cluster.centroid IS NOT NULL
GROUP BY cluster.id, signal.name
ORDER BY signal.name, cluster.id
"""

# Each member's Laminar trace, for matching our clusters to Laminar's.
MEMBER_TRACES_SQL = """
SELECT member.cluster_id, member.event_id, trace.laminar_trace_id, trace.started_at
FROM cluster_members member
JOIN signal_events event ON event.id = member.event_id
JOIN traces trace ON trace.id = event.trace_id
ORDER BY member.cluster_id, member.assigned_at, member.event_id
"""

LAMINAR_EVENTS_SQL = """
SELECT trace_id, signal_name, cluster_details
FROM signal_events_all
WHERE timestamp >= toDateTime64({since:String}, 6, 'UTC')
"""


@dataclass
class Tried:
    cluster_id: int
    signal: str
    size: int
    todays_name: str | None
    closest_summaries: list
    laminar: str | None = None                  # "Name (k of n traces)", or None
    answers: dict = field(default_factory=dict)  # model -> {name, seconds, input/output tokens} or {error}
    # --rename-from: model -> {at_n, renamed, fresh, kept, seconds, input/output tokens} or {error}


# ------------------------------------------------------------------ Laminar's names (pure)

def laminar_leaf_name(cluster_details):
    """The most specific named cluster in one Laminar event's chain ({id, name, level} dicts;
    level 0 is Laminar's internal unnamed bucket), or None."""
    if isinstance(cluster_details, str):
        try:
            cluster_details = json.loads(cluster_details)
        except ValueError:
            return None
    named = [node for node in cluster_details or []
             if isinstance(node, dict) and node.get("name") and (node.get("level") or 0) >= 1]
    return min(named, key=lambda node: node["level"])["name"] if named else None


def laminar_majority(trace_ids, laminar_events, signal_name):
    """'Name (k of n traces)' for the Laminar cluster most of these traces landed in, for the
    Laminar Signal named like ours (ignoring case). n = traces Laminar named at all for that
    Signal. None if Laminar named none of them."""
    wanted = signal_name.lower()
    name_of_trace = {}
    for event in laminar_events:
        if (event.get("signal_name") or "").lower() != wanted:
            continue
        name = laminar_leaf_name(event.get("cluster_details"))
        trace_id = str(event.get("trace_id"))
        if name and trace_id in trace_ids:
            name_of_trace.setdefault(trace_id, name)   # a trace with two findings: count it once
    if not name_of_trace:
        return None
    counts = Counter(name_of_trace.values())
    top = max(counts, key=lambda name: (counts[name], name))
    return f"{top} ({counts[top]} of {len(name_of_trace)} traces)"


def read_laminar_events(since):
    from ..poller.laminar import LaminarSQL
    client = LaminarSQL(api_key=os.environ["LMNR_PROJECT_API_KEY"],
                        api_url=os.environ.get("LMNR_API_URL", "https://api.lmnr.ai"))
    try:
        moment = since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        return client.query(LAMINAR_EVENTS_SQL, {"since": moment})
    finally:
        client.close()


# ------------------------------------------------------------------ asking

def first_members(cluster, event_ids):
    """The cluster input cut down to the members in `event_ids` (its first N), same order."""
    kept = tuple(member for member in cluster.members if member.event_id in event_ids)
    return replace(cluster, members=kept, member_count=len(kept))


def ask_all(llm, models, prompt, cluster, tried):
    """Every model's name for one cluster. A model's LLMError is recorded, not raised; anything
    else (a wrong model name, a bad key) stops the run loudly."""
    for model in models:
        started = time.monotonic()
        try:
            name, usage = ask_llm(llm, model, cluster, prompt)
            tried.answers[model] = {"name": name, "seconds": round(time.monotonic() - started, 1),
                                    "input_tokens": usage.input_tokens,
                                    "output_tokens": usage.output_tokens}
        except LLMError as error:
            tried.answers[model] = {"error": f"{type(error).__name__}: {error}"}
    return tried


def ask_rename(llm, models, prompt, cluster, first_ids, tried):
    """--rename-from: per model, the name at the first N members, the rename at full size with
    that name as the current LLM name, and a fresh full-size name. Three calls per model."""
    small = first_members(cluster, first_ids)
    for model in models:
        started = time.monotonic()
        try:
            at_n, small_usage = ask_llm(llm, model, small, prompt)
            as_rename = replace(cluster, job=replace(cluster.job, kind="rename", current_name=at_n,
                                                     current_source="llm"))
            renamed, rename_usage = ask_llm(llm, model, as_rename, prompt)
            fresh, fresh_usage = ask_llm(llm, model, cluster, prompt)
        except LLMError as error:
            tried.answers[model] = {"error": f"{type(error).__name__}: {error}"}
            continue
        usages = (small_usage, rename_usage, fresh_usage)
        tried.answers[model] = {"at_n": at_n, "renamed": renamed, "fresh": fresh,
                                "kept": renamed == at_n,
                                "seconds": round(time.monotonic() - started, 1),
                                "input_tokens": sum(u.input_tokens for u in usages),
                                "output_tokens": sum(u.output_tokens for u in usages)}
    return tried


# ------------------------------------------------------------------ report (pure)

def model_stats(tried, model):
    answers = [t.answers.get(model, {}) for t in tried]
    good = [a for a in answers if "error" not in a and a]
    words = [len((a.get("name") or a.get("renamed")).split()) for a in good]
    seconds = [a["seconds"] for a in good]
    input_tokens = sum(a["input_tokens"] for a in good)
    output_tokens = sum(a["output_tokens"] for a in good)
    rates = PRICES.get(model)
    cost = (round((input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000, 4)
            if rates else None)
    return {"named": len(good), "errors": len(answers) - len(good),
            "kept": sum(1 for a in good if a.get("kept")),
            "avg_words": round(sum(words) / len(words), 1) if words else None,
            "avg_seconds": round(sum(seconds) / len(seconds), 1) if seconds else None,
            "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}


def _cell(text):
    return (text or "").replace("|", "\\|")


def render_report(tried, models, meta):
    lines = [f"# Cluster names side by side ({meta['taken_at']})", "",
             f"Prompt v{meta['prompt_version']} · {len(tried)} clusters"
             + (f" · named from each cluster's first {meta['as_of']} members" if meta["as_of"] else "")
             + (" · Laminar not read" if not meta["laminar"] else ""), "",
             "| model | named | errors | avg words | avg seconds | tokens in / out | cost (USD) |",
             "|---|---|---|---|---|---|---|"]
    for model in models:
        s = model_stats(tried, model)
        cost = s["cost_usd"] if s["cost_usd"] is not None else "not priced"
        lines.append(f"| {model} | {s['named']} | {s['errors']} | {s['avg_words']} | "
                     f"{s['avg_seconds']} | {s['input_tokens']} / {s['output_tokens']} | {cost} |")

    by_signal = defaultdict(list)
    for t in tried:
        by_signal[t.signal].append(t)
    for signal, clusters in by_signal.items():
        lines += ["", f"## {signal} ({len(clusters)} clusters)", "",
                  "| cluster | size | today | " + " | ".join(models) + " | Laminar |",
                  "|---" * (len(models) + 4) + "|"]
        for t in clusters:
            names = [t.answers.get(m, {}).get("name") or "**error**" for m in models]
            lines.append(f"| {t.cluster_id} | {t.size} | {_cell(t.todays_name)} | "
                         + " | ".join(_cell(n) for n in names) + f" | {_cell(t.laminar) or '-'} |")
        lines.append("")
        for t in clusters:
            lines += [f"<details><summary>Cluster {t.cluster_id}: closest members</summary>", ""]
            lines += [f"- {summary}" for summary in t.closest_summaries]
            errors = [f"- {m}: {a['error']}" for m, a in t.answers.items() if "error" in a]
            lines += (["", "Errors:"] + errors if errors else []) + ["", "</details>", ""]
    return "\n".join(lines)


def render_rename_report(tried, models, meta):
    n = meta["rename_from"]
    lines = [f"# Renames side by side ({meta['taken_at']})", "",
             f"Prompt v{meta['prompt_version']} · {len(tried)} clusters with at least {2 * n} members · "
             f"each named from its first {n} members, then renamed at full size with that name as "
             "the current name, and named fresh at full size", "",
             "| model | clusters | rename kept the name | rename changed it | errors | tokens in / out | cost (USD) |",
             "|---|---|---|---|---|---|---|"]
    for model in models:
        s = model_stats(tried, model)
        cost = s["cost_usd"] if s["cost_usd"] is not None else "not priced"
        lines.append(f"| {model} | {s['named']} | {s['kept']} | {s['named'] - s['kept']} | "
                     f"{s['errors']} | {s['input_tokens']} / {s['output_tokens']} | {cost} |")
    by_signal = defaultdict(list)
    for t in tried:
        by_signal[t.signal].append(t)
    for signal, clusters in by_signal.items():
        lines += ["", f"## {signal} ({len(clusters)} clusters)", "",
                  f"| cluster | size | model | at {n} members | renamed at full size | kept? | fresh at full size |",
                  "|---|---|---|---|---|---|---|"]
        for t in clusters:
            for model in models:
                a = t.answers.get(model, {})
                if "error" in a:
                    lines.append(f"| {t.cluster_id} | {t.size} | {model} | **error** {_cell(a['error'])} | | | |")
                    continue
                lines.append(f"| {t.cluster_id} | {t.size} | {model} | {_cell(a.get('at_n'))} | "
                             f"{_cell(a.get('renamed'))} | {'kept' if a.get('kept') else 'changed'} | "
                             f"{_cell(a.get('fresh'))} |")
    return "\n".join(lines)


def parse_models(text):
    models = [part.strip() for part in text.split(",") if part.strip()]
    if not models:
        raise argparse.ArgumentTypeError("give at least one model")
    return models


# ------------------------------------------------------------------ main

def load_clusters(conn, signal_filter, as_of, rename_from=None):
    """(Tried, ClusterInput) per cluster that has something to show the model, plus each
    cluster's member traces (for Laminar) and the earliest trace time."""
    members_by_cluster = defaultdict(list)
    earliest = None
    for cluster_id, event_id, laminar_trace_id, started_at in conn.execute(MEMBER_TRACES_SQL):
        members_by_cluster[cluster_id].append((event_id, str(laminar_trace_id)))
        earliest = started_at if earliest is None else min(earliest, started_at)

    loaded = []
    for cluster_id, signal_id, signal, todays_name, size in conn.execute(CLUSTERS_SQL).fetchall():
        if signal_filter and signal.lower() != signal_filter.lower():
            continue
        cluster = load_input(conn, Job(cluster_id, signal_id, "new"))
        if cluster is None:                    # every member deleted since the first read
            continue
        members = members_by_cluster[cluster_id]
        if as_of:
            members = members[:as_of]
            cluster = first_members(cluster, {event_id for event_id, _ in members})
        summaries = [m.summary for m in cluster.members if m.summary is not None]
        if not summaries:
            continue
        first_ids = None
        if rename_from:
            if cluster.member_count < 2 * rename_from:     # the clusterer wouldn't rename it yet
                continue
            first_ids = {event_id for event_id, _ in members[:rename_from]}
        tried = Tried(cluster_id, signal, cluster.member_count, todays_name, summaries[:5])
        loaded.append((tried, cluster, {trace_id for _, trace_id in members}, first_ids))
    return loaded, earliest


def main(argv=None):
    from .llm_client import OpenAIClient
    from .loop import open_connection

    load_dotenv()
    parser = argparse.ArgumentParser(description="Try LLM cluster names side by side.")
    parser.add_argument("--database-url", required=True,
                        help="the database to read (required: never taken from DATABASE_URL)")
    parser.add_argument("--models", type=parse_models, default=DEFAULT_MODELS,
                        help=f"comma-separated models (default {','.join(DEFAULT_MODELS)})")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--as-of", type=int, help="name each cluster from its first N members only")
    which.add_argument("--rename-from", type=int,
                       help="name from the first N members, then rename at full size (renames check)")
    parser.add_argument("--signal", help="only this Signal's clusters")
    parser.add_argument("--no-laminar", action="store_true", help="don't read Laminar's names")
    parser.add_argument("--workers", type=int, default=4, help="clusters named in parallel (default 4)")
    parser.add_argument("--out", type=Path,
                        default=Path(f"try_naming_{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"))
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        parser.error("OPENAI_API_KEY is not set")
    for flag, value in (("--as-of", args.as_of), ("--rename-from", args.rename_from)):
        if value is not None and value < 1:
            parser.error(f"{flag} must be at least 1")
    use_laminar = not args.no_laminar
    if use_laminar and not os.environ.get("LMNR_PROJECT_API_KEY"):
        parser.error("LMNR_PROJECT_API_KEY is not set (or pass --no-laminar)")

    prompt = load_naming_prompt()
    with open_connection(args.database_url) as conn:
        loaded, earliest = load_clusters(conn, args.signal, args.as_of, args.rename_from)
    if not loaded:
        parser.error("no clusters to name in that database"
                     + (f" (--rename-from needs clusters of at least {2 * args.rename_from} members)"
                        if args.rename_from else ""))

    if use_laminar:
        events = read_laminar_events(earliest - timedelta(hours=1))
        for tried, _, trace_ids, _ in loaded:
            tried.laminar = laminar_majority(trace_ids, events, tried.signal)

    args.out.mkdir(parents=True, exist_ok=False)      # before paying for calls: fail early if it exists
    calls = 3 if args.rename_from else 1
    print(f"{len(loaded)} clusters x {len(args.models)} models x {calls} call(s) ...")
    llm = OpenAIClient(api_key)

    def ask(item):
        tried, cluster, _, first_ids = item
        if args.rename_from:
            return ask_rename(llm, args.models, prompt, cluster, first_ids, tried)
        return ask_all(llm, args.models, prompt, cluster, tried)

    with ThreadPoolExecutor(args.workers) as pool:
        tried = list(pool.map(ask, loaded))

    meta = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "prompt_version": prompt.version, "models": args.models, "as_of": args.as_of,
            "rename_from": args.rename_from, "laminar": use_laminar}
    (args.out / "results.json").write_text(json.dumps(
        {"meta": meta, "clusters": [t.__dict__ for t in tried]}, indent=2, default=str))
    render = render_rename_report if args.rename_from else render_report
    (args.out / "report.md").write_text(render(tried, args.models, meta))
    print(f"Done: {args.out}/report.md")


if __name__ == "__main__":
    main()
