"""Try summarizer prompt versions side by side on real events (SIG-64). A developer tool.

    python -m tripwire.clusterer.try_prompts --database-url URL [--versions 1,2,3] [--per-cluster 2]
                                             [--unclustered 3] [--seed 1] [--out DIR]

Picks a sample of already-summarized events (a few from each current cluster, plus a few that sit in
no cluster), asks the summarizer model for a summary under each prompt version, and writes
DIR/results.json plus DIR/report.md: per event, the stored summary next to each version's answer.

Reads the database only; never writes to it, so it is safe on a database the pipeline is using.
`--database-url` must be given on the command line (never taken from DATABASE_URL), like report.py.
Re-running the stored version (v1 by default) on purpose shows how much a summary changes with no
prompt change at all, so a difference smaller than that isn't a real difference.

Needs OPENAI_API_KEY. Model: SUMMARIZER_MODEL (same default as the clusterer) or --model.
"""

import argparse
import json
import os
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from ..llm import LLMError
from .settings import DEFAULTS
from .sql import event_signal
from .summarize import build_messages, clean_details, load_event, load_prompt

SAMPLE_SQL = f"""
SELECT e.id, s.name, c.id, c.name, summary.prompt_version, summary.summary,
       e.description, e.payload
FROM signal_events e
{event_signal("e", "v")}
JOIN signals s ON s.id = v.signal_id
JOIN event_summaries summary ON summary.event_id = e.id AND summary.status = 'done'
LEFT JOIN cluster_members member ON member.event_id = e.id
LEFT JOIN clusters c ON c.id = member.cluster_id
ORDER BY e.id
"""


@dataclass
class Sampled:
    event_id: int
    signal: str
    cluster_id: int | None
    cluster_name: str | None
    stored_version: int
    stored_summary: str
    finding: str                      # the judge's description or payload, for context
    answers: dict = field(default_factory=dict)   # version -> {summary, cause, details} or {error}


def pick_sample(rows, per_cluster, unclustered, seed):
    """A few events from every cluster of every signal, plus a few in no cluster, in a stable order."""
    groups = defaultdict(list)
    for event_id, signal, cluster_id, cluster_name, version, summary, description, payload in rows:
        finding = (description or "").strip() or json.dumps(payload or {})
        groups[(signal, cluster_id)].append(
            Sampled(event_id, signal, cluster_id, cluster_name, version, summary, finding[:300]))
    rng = random.Random(seed)
    sample = []
    for (signal, cluster_id), events in sorted(groups.items(), key=lambda g: (g[0][0], g[0][1] is None, g[0][1] or 0)):
        take = per_cluster if cluster_id is not None else unclustered
        sample += sorted(rng.sample(events, min(take, len(events))), key=lambda e: e.event_id)
    return sample


def ask(conn_url, llm, model, prompts, event):
    """Every version's answer for one event. One connection per worker thread."""
    from .loop import open_connection
    with open_connection(conn_url) as conn:
        loaded = load_event(conn, event.event_id)
    for version, prompt in prompts.items():
        try:
            result = llm.complete(model, build_messages(loaded, prompt.system), prompt.schema)
            details, warning = clean_details(result.output)
            event.answers[version] = {"cause": result.output.get("cause"),
                                      "summary": result.output.get("summary"),
                                      "details": details, "warning": warning,
                                      "cost_usd": result.usage.cost_usd}
        except LLMError as error:
            event.answers[version] = {"error": f"{type(error).__name__}: {error}"}
    return event


def version_stats(sample, version):
    answers = [e.answers.get(version, {}) for e in sample]
    good = [a for a in answers if "summary" in a]
    lengths = [len(a["summary"] or "") for a in good]
    with_details = [a for a in good if a.get("details") is not None]
    return {
        "answered": len(good), "errors": len(answers) - len(good),
        "avg_summary_chars": round(sum(lengths) / len(lengths)) if lengths else None,
        "events_with_no_details": sum(1 for a in with_details if not a["details"]) if with_details else None,
        "avg_details": (round(sum(len(a["details"]) for a in with_details) / len(with_details), 1)
                        if with_details else None),
        "cost_usd": round(sum(a.get("cost_usd") or 0 for a in good), 4),
    }


def render_report(sample, versions, meta):
    lines = [f"# Summarizer prompts side by side ({meta['taken_at']})", "",
             f"Model `{meta['model']}` · versions {', '.join(f'v{v}' for v in versions)} · "
             f"{len(sample)} events · seed {meta['seed']}", "",
             "| version | answered | errors | avg summary chars | events with no details | avg details | cost (USD) |",
             "|---|---|---|---|---|---|---|"]
    for version in versions:
        s = version_stats(sample, version)
        lines.append(f"| v{version} | {s['answered']} | {s['errors']} | {s['avg_summary_chars']} | "
                     f"{s['events_with_no_details'] if s['events_with_no_details'] is not None else '-'} | "
                     f"{s['avg_details'] if s['avg_details'] is not None else '-'} | {s['cost_usd']} |")
    lines.append("")
    current = None
    for event in sample:
        group = (event.signal, event.cluster_id)
        if group != current:
            current = group
            where = (f"cluster {event.cluster_id}: {event.cluster_name}" if event.cluster_id is not None
                     else "in no cluster")
            lines += [f"## {event.signal} · {where}", ""]
        lines += [f"**Event {event.event_id}** · finding: {event.finding}", "",
                  f"- stored v{event.stored_version}: {event.stored_summary}"]
        for version in versions:
            answer = event.answers.get(version, {})
            label = f"v{version}" + (" (re-run)" if version == event.stored_version else "")
            if "error" in answer:
                lines.append(f"- {label}: **error** {answer['error']}")
                continue
            line = f"- {label}: {answer.get('summary')}"
            if answer.get("details") is not None:
                line += " · details: " + ("; ".join(answer["details"]) or "(none)")
            if answer.get("warning"):
                line += f" · warning: {answer['warning']}"
            lines.append(line)
        lines.append("")
    return "\n".join(lines)


def parse_versions(text):
    try:
        versions = [int(part) for part in text.split(",") if part.strip()]
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"versions must be numbers separated by commas: {error}")
    if not versions:
        raise argparse.ArgumentTypeError("give at least one version")
    return versions


def main(argv=None):
    from .llm_client import OpenAIClient
    from .loop import open_connection

    load_dotenv()
    parser = argparse.ArgumentParser(description="Try summarizer prompt versions side by side.")
    parser.add_argument("--database-url", required=True,
                        help="the database to read (required: never taken from DATABASE_URL)")
    parser.add_argument("--versions", type=parse_versions, default=[1, 2, 3],
                        help="comma-separated prompt versions (default 1,2,3)")
    parser.add_argument("--per-cluster", type=int, default=2, help="events from each cluster (default 2)")
    parser.add_argument("--unclustered", type=int, default=3,
                        help="events in no cluster, per signal (default 3)")
    parser.add_argument("--seed", type=int, default=1, help="sample seed (default 1)")
    parser.add_argument("--model", default=os.environ.get("SUMMARIZER_MODEL", DEFAULTS["SUMMARIZER_MODEL"][0]))
    parser.add_argument("--workers", type=int, default=4, help="parallel events (default 4)")
    parser.add_argument("--out", type=Path,
                        default=Path(f"try_prompts_{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"))
    args = parser.parse_args(argv)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        parser.error("OPENAI_API_KEY is not set")
    try:
        prompts = {version: load_prompt(version) for version in args.versions}
    except FileNotFoundError as error:
        parser.error(f"no such prompt version: {error.filename}")

    with open_connection(args.database_url) as conn:
        sample = pick_sample(conn.execute(SAMPLE_SQL).fetchall(), args.per_cluster, args.unclustered, args.seed)
    if not sample:
        parser.error("no summarized events found in that database")
    args.out.mkdir(parents=True, exist_ok=False)      # before paying for calls: fail early if it exists
    print(f"{len(sample)} events x {len(prompts)} versions with {args.model} ...")

    llm = OpenAIClient(api_key)
    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(lambda event: ask(args.database_url, llm, args.model, prompts, event), sample))

    meta = {"taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": args.model,
            "seed": args.seed, "versions": args.versions}
    (args.out / "results.json").write_text(json.dumps(
        {"meta": meta, "events": [event.__dict__ for event in sample]}, indent=2, default=str))
    (args.out / "report.md").write_text(render_report(sample, args.versions, meta))
    print(f"Done: {args.out}/report.md")


if __name__ == "__main__":
    main()
