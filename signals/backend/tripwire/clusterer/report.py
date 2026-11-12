"""Calibration report: how well the clusters match an answer key (DESIGN.md Q14, grouping PR 6).

    python -m tripwire.clusterer.report --database-url URL --answer-key runs/<run>/answer_key.jsonl [--sweep]

A developer tool, never run by the deployed product. It only reads the database (the sweep writes
to a throwaway schema, see sweep.py). `--database-url` must be given on the command line; the
tool never falls back to DATABASE_URL, so it can't be pointed at a real deployment by accident.

The answer key comes from park-traffic (planted bugs). Its `bugs_by_turn` lists each turn's
Laminar trace ID and the bugs that fired in that turn, so each event gets the bugs of its own
turn: event -> its trace -> that turn's bugs (decided Nov 12, Decision 1). A bug is recorded on
the turn where it fires, even when its effect shows in a later turn; events whose turn had no bug
are left out of the scores and listed separately rather than guessed.

Scores (decided Nov 12, Decision 3; research and a test of both in the work log):
  * Extended BCubed precision and recall (Amigó et al. 2009) are the main scores. Each event is
    asked "are my cluster-mates my bug?" (precision) and "are the events with my bug my
    cluster-mates?" (recall), then the answers are averaged. Unlike pair counting, one big
    cluster can't outweigh the rest, and an event with two bugs is handled by the metric itself.
  * The suggested threshold has the best F0.5 (precision counts twice as much as recall): with
    F1, both BCubed and pairwise scores preferred a "magnet" cluster that mixes several problems
    over an honest split of one problem, the prototype's real failure.
  * Pairwise precision and recall are shown too, for comparison with the prototype's report.
  * An event left in the pool counts as alone: it lowers recall, since users don't see it grouped.
Scores are per Signal (clusters belong to a Signal, Q6) and per summarizer prompt version.
"""

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .sql import event_signal, in_the_pool


class AnswerKeyError(ValueError):
    """The answer key can't be used (missing per-turn bugs, unreadable)."""


@dataclass(frozen=True)
class LabeledEvent:
    event_id: int
    signal_id: int
    prompt_version: int | None   # of its summary; None if it has none
    cluster_id: int | None       # None = in the pool
    bugs: frozenset              # bugs that fired in its turn; empty = none

    @property
    def group(self):
        """What scores are computed per: (Signal, summarizer prompt version)."""
        return (self.signal_id, self.prompt_version)


@dataclass(frozen=True)
class Scores:
    precision: float
    recall: float

    def f_beta(self, beta):
        """F-beta: beta < 1 counts precision more (F0.5: twice as much), 1 = F1 (equal weight)."""
        if self.precision == 0 and self.recall == 0:
            return 0.0
        weight = beta * beta
        return (1 + weight) * self.precision * self.recall / (weight * self.precision + self.recall)

    @property
    def f1(self):
        return self.f_beta(1.0)

    @property
    def f_half(self):
        return self.f_beta(0.5)


# ------------------------------------------------------------------ answer key

def load_answer_key(paths):
    """{laminar_trace_id: frozenset of bugs fired in that turn} from park-traffic answer keys."""
    bugs_by_trace = {}
    for path in paths:
        for line_number, line in enumerate(Path(path).read_text().splitlines(), start=1):
            if not line.strip():
                continue
            conversation = json.loads(line)
            if "bugs_by_turn" not in conversation:
                raise AnswerKeyError(
                    f"{path} line {line_number} has no bugs_by_turn: it was made by an older park-traffic. "
                    "Rerun the traffic with a park-traffic that records bugs per turn."
                )
            for turn in conversation["bugs_by_turn"]:
                if turn["trace_id"] is not None:          # None: a dry run, nothing was sent
                    bugs_by_trace[turn["trace_id"]] = frozenset(turn["bugs"])
    return bugs_by_trace


# ------------------------------------------------------------------ scores

def _cluster_keys(events, clusters):
    """Each event's cluster, with pool events each alone in their own "cluster"."""
    keys = []
    for event in events:
        # A replay's cluster IDs are its own, so never mix in the live ones.
        cluster_id = clusters.get(event.event_id) if clusters is not None else event.cluster_id
        keys.append(("pool", event.event_id) if cluster_id is None else ("cluster", cluster_id))
    return keys


def bcubed(events, clusters=None):
    """Extended BCubed precision and recall over events that have at least one bug.

    `clusters` ({event_id: cluster_id or None}) replaces the events' own clusters, for the sweep.
    With one cluster per event, Amigó et al.'s multiplicity terms reduce to: a cluster-mate counts
    for precision if it shares any bug; a same-bug event counts for recall, divided by how many
    bugs the two share, if it's in the same cluster.
    """
    labeled = [event for event in events if event.bugs]
    if not labeled:
        return Scores(precision=1.0, recall=1.0)
    keys = _cluster_keys(labeled, clusters)
    precision_total = recall_total = 0.0
    for index, event in enumerate(labeled):
        mates = [other for other_index, other in enumerate(labeled) if keys[other_index] == keys[index]]
        precision_total += sum(1 for other in mates if event.bugs & other.bugs) / len(mates)
        sharing = [(other_index, other) for other_index, other in enumerate(labeled) if event.bugs & other.bugs]
        recall_total += sum(
            1 / len(event.bugs & other.bugs) for other_index, other in sharing if keys[other_index] == keys[index]
        ) / len(sharing)
    return Scores(precision=precision_total / len(labeled), recall=recall_total / len(labeled))


def pairwise(events, clusters=None):
    """Pairwise precision and recall: of pairs in one cluster, the share sharing a bug; of pairs
    sharing a bug, the share in one cluster. As in the prototype's report."""
    labeled = [event for event in events if event.bugs]
    keys = _cluster_keys(labeled, clusters)
    together = sharing = both = 0
    for first in range(len(labeled)):
        for second in range(first + 1, len(labeled)):
            same_cluster = keys[first] == keys[second]
            same_bug = bool(labeled[first].bugs & labeled[second].bugs)
            together += same_cluster
            sharing += same_bug
            both += same_cluster and same_bug
    return Scores(precision=both / together if together else 1.0,
                  recall=both / sharing if sharing else 1.0)


def by_group(events):
    """{(signal_id, prompt_version): [events]}, in a stable order."""
    groups = {}
    for event in sorted(events, key=lambda event: (event.signal_id, event.prompt_version or 0, event.event_id)):
        groups.setdefault(event.group, []).append(event)
    return groups


def suggest_threshold(rows):
    """The threshold with the best BCubed F0.5. `rows`: [(threshold, Scores)]. A tie goes to the
    lower threshold (stricter, so cleaner clusters)."""
    if not rows:
        return None
    best = max(row[1].f_half for row in rows)
    return min(threshold for threshold, scores in rows if scores.f_half == best)


# ------------------------------------------------------------------ reading the database

EVENTS_SQL = f"""
SELECT sig_event.id, version.signal_id, trace.laminar_trace_id, summary.prompt_version,
       member.cluster_id
FROM signal_events sig_event
{event_signal("sig_event", "version")}
JOIN traces trace ON trace.id = sig_event.trace_id
JOIN event_embeddings embedded ON embedded.event_id = sig_event.id
LEFT JOIN event_summaries summary ON summary.event_id = sig_event.id
LEFT JOIN cluster_members member ON member.event_id = sig_event.id
WHERE trace.laminar_trace_id = ANY(%s)
ORDER BY sig_event.id
"""


def load_events(conn, bugs_by_trace):
    """The embedded events of the answer key's traces, labeled with their turn's bugs. Events of
    other traffic, and events that never got an embedding (see the funnel), aren't included."""
    # The column is a uuid (psycopg returns UUID objects); the answer key has text.
    return [
        LabeledEvent(event_id, signal_id, prompt_version, cluster_id, bugs_by_trace[str(laminar_trace_id)])
        for event_id, signal_id, laminar_trace_id, prompt_version, cluster_id
        in conn.execute(EVENTS_SQL, (list(bugs_by_trace),))
    ]


FUNNEL_SQL = """
SELECT
    (SELECT count(*) FROM traces trace WHERE trace.laminar_trace_id = ANY(%(traces)s)),
    (SELECT count(*) FROM signal_runs run JOIN traces trace ON trace.id = run.trace_id
     WHERE trace.laminar_trace_id = ANY(%(traces)s)),
    (SELECT count(*) FROM signal_runs run JOIN traces trace ON trace.id = run.trace_id
     WHERE trace.laminar_trace_id = ANY(%(traces)s) AND run.status = 'failed'),
    (SELECT count(*) FROM signal_events sig_event JOIN traces trace ON trace.id = sig_event.trace_id
     WHERE trace.laminar_trace_id = ANY(%(traces)s)),
    (SELECT count(*) FROM event_summaries summary
     JOIN signal_events sig_event ON sig_event.id = summary.event_id
     JOIN traces trace ON trace.id = sig_event.trace_id
     WHERE trace.laminar_trace_id = ANY(%(traces)s) AND summary.status = 'done'),
    (SELECT count(*) FROM event_summaries summary
     JOIN signal_events sig_event ON sig_event.id = summary.event_id
     JOIN traces trace ON trace.id = sig_event.trace_id
     WHERE trace.laminar_trace_id = ANY(%(traces)s) AND summary.status = 'failed'),
    (SELECT count(*) FROM event_embeddings embedded
     JOIN signal_events sig_event ON sig_event.id = embedded.event_id
     JOIN traces trace ON trace.id = sig_event.trace_id
     WHERE trace.laminar_trace_id = ANY(%(traces)s)),
    (SELECT count(*) FROM cluster_members member
     JOIN signal_events sig_event ON sig_event.id = member.event_id
     JOIN traces trace ON trace.id = sig_event.trace_id
     WHERE trace.laminar_trace_id = ANY(%(traces)s))
"""

FUNNEL_STEPS = ("traces", "runs", "runs failed", "events", "summaries done", "summaries failed",
                "embedded", "in a cluster")


def load_funnel(conn, bugs_by_trace):
    """How far the answer key's traffic got: {step: count}, plus "in the pool"."""
    counts = dict(zip(FUNNEL_STEPS, conn.execute(FUNNEL_SQL, {"traces": list(bugs_by_trace)}).fetchone()))
    counts["in the pool"] = counts["embedded"] - counts["in a cluster"]
    counts["answer key turns"] = len(bugs_by_trace)
    return counts


CLUSTER_NAMES_SQL = "SELECT id, name FROM clusters WHERE id = ANY(%s)"

NEAREST_SQL = f"""
SELECT pooled.event_id, neighbour.event_id, neighbour.distance
FROM event_embeddings pooled
JOIN signal_events sig_event ON sig_event.id = pooled.event_id
{event_signal("sig_event", "version")}
CROSS JOIN LATERAL (
    SELECT other.event_id, other.embedding <=> pooled.embedding AS distance
    FROM event_embeddings other
    JOIN signal_events other_event ON other_event.id = other.event_id
    {event_signal("other_event", "other_version")}
    WHERE other_version.signal_id = version.signal_id AND other.event_id <> pooled.event_id
    ORDER BY distance, other.event_id
    LIMIT 1
) neighbour
WHERE {in_the_pool("pooled")} AND pooled.event_id = ANY(%s)
ORDER BY pooled.event_id
"""


def load_nearest(conn, event_ids):
    """For pool events: {event_id: (nearest other event of its Signal, distance)}. Shows near misses."""
    return {event_id: (neighbour_id, distance)
            for event_id, neighbour_id, distance in conn.execute(NEAREST_SQL, (list(event_ids),))}


# ------------------------------------------------------------------ printing

def bug_name(bugs):
    return "+".join(sorted(bugs)) if bugs else "(no bug in its turn)"


def scores_line(label, events, clusters=None):
    cube, pair = bcubed(events, clusters), pairwise(events, clusters)
    return (f"{label:>9}  BCubed P {cube.precision:.2f} R {cube.recall:.2f} F1 {cube.f1:.2f} "
            f"F0.5 {cube.f_half:.2f}  |  pairwise P {pair.precision:.2f} R {pair.recall:.2f}")


def render_funnel(funnel):
    lines = ["Funnel (answer key traffic only)"]
    lines += [f"  {step:<18} {funnel[step]}" for step in ("answer key turns", *FUNNEL_STEPS, "in the pool")]
    return "\n".join(lines)


def render_bug_table(events, names):
    """Bug x cluster table for one group: each cluster's name, size and its events per bug."""
    bug_columns = sorted({bug_name(event.bugs) for event in events})
    rows = {}
    for event in events:
        rows.setdefault(event.cluster_id, Counter())[bug_name(event.bugs)] += 1
    width = max([len(column) for column in bug_columns] + [5])
    lines = ["  " + f"{'cluster':<48}" + "".join(f"{column:>{width + 2}}" for column in bug_columns)]
    for cluster_id in sorted(rows, key=lambda cluster: (cluster is None, cluster or 0)):
        label = "(pool: not in a cluster)" if cluster_id is None else f"#{cluster_id} {names.get(cluster_id) or '(unnamed)'}"
        label = label if len(label) <= 46 else label[:45] + "…"
        lines.append("  " + f"{label:<48}" + "".join(f"{rows[cluster_id][column]:>{width + 2}}" for column in bug_columns))
    return "\n".join(lines)


def render_nearest(events, nearest):
    by_id = {event.event_id: event for event in events}
    lines = ["  Pool events and their nearest neighbour (near misses):"]
    for event in events:
        if event.cluster_id is not None:
            continue
        if event.event_id not in nearest:
            lines.append(f"    event {event.event_id} [{bug_name(event.bugs)}] -> no other event of its Signal yet")
            continue
        neighbour_id, distance = nearest[event.event_id]
        neighbour = by_id.get(neighbour_id)
        neighbour_bugs = bug_name(neighbour.bugs) if neighbour else "(not in the answer key)"
        lines.append(f"    event {event.event_id} [{bug_name(event.bugs)}] -> event {neighbour_id} "
                     f"[{neighbour_bugs}] at {distance:.3f}")
    return "\n".join(lines) if len(lines) > 1 else "  No pool events."


def render_sweep(group_events, results):
    """`results`: [(threshold, {event_id: cluster_id or None})] for one group's events."""
    rows = [(threshold, bcubed(group_events, clusters)) for threshold, clusters in results]
    suggested = suggest_threshold(rows)
    lines = ["  Sweep (replay of the real clusterer at each threshold):",
             "    threshold  clusters  clustered   BCubed P    R   F1  F0.5   pairwise P    R"]
    for threshold, clusters in results:
        cube, pair = bcubed(group_events, clusters), pairwise(group_events, clusters)
        cluster_ids = {clusters.get(event.event_id) for event in group_events} - {None}
        clustered = sum(1 for event in group_events if clusters.get(event.event_id) is not None)
        mark = "  <- suggested (best F0.5)" if threshold == suggested else ""
        lines.append(f"    {threshold:>9.2f}  {len(cluster_ids):>8}  {clustered:>4}/{len(group_events):<4}"
                     f"   {cube.precision:>8.2f} {cube.recall:>4.2f} {cube.f1:>4.2f} {cube.f_half:>5.2f}"
                     f"   {pair.precision:>10.2f} {pair.recall:>4.2f}{mark}")
    return "\n".join(lines)


def build_report(conn, bugs_by_trace, sweep_results=None):
    """The whole report as text. `sweep_results`: [(threshold, {event_id: cluster_id or None})]."""
    events = load_events(conn, bugs_by_trace)
    sections = [render_funnel(load_funnel(conn, bugs_by_trace))]
    if not events:
        sections.append("No embedded events from the answer key's traces yet.")
        return "\n\n".join(sections)
    names = dict(conn.execute(CLUSTER_NAMES_SQL, (list({event.cluster_id for event in events} - {None}),)))
    nearest = load_nearest(conn, [event.event_id for event in events if event.cluster_id is None])
    for (signal_id, prompt_version), group_events in by_group(events).items():
        unlabeled = sum(1 for event in group_events if not event.bugs)
        lines = [f"Signal {signal_id}, summarizer prompt v{prompt_version}: {len(group_events)} events "
                 f"({unlabeled} with no bug in their turn, left out of the scores)",
                 scores_line("now", group_events),
                 render_bug_table(group_events, names),
                 render_nearest(group_events, nearest)]
        if sweep_results:
            lines.append(render_sweep(group_events, sweep_results))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def parse_thresholds(text):
    try:
        thresholds = sorted({round(float(value), 4) for value in text.split(",") if value.strip()})
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"thresholds must be numbers separated by commas: {error}")
    if not thresholds or not all(0 < threshold <= 1 for threshold in thresholds):
        raise argparse.ArgumentTypeError("thresholds must be between 0 and 1 (cosine distances)")
    return thresholds


def main(argv=None):
    from . import sweep
    from .loop import open_connection

    parser = argparse.ArgumentParser(description="Score clusters against a park-traffic answer key.")
    parser.add_argument("--database-url", required=True,
                        help="the database to read (required: never taken from DATABASE_URL)")
    parser.add_argument("--answer-key", required=True, action="append",
                        help="park-traffic answer_key.jsonl (repeat for several runs)")
    parser.add_argument("--sweep", action="store_true",
                        help="also replay the clusterer at other thresholds, in a throwaway schema")
    parser.add_argument("--thresholds", type=parse_thresholds, default=sweep.default_thresholds(),
                        help="comma-separated thresholds for --sweep (default 0.20 to 0.40 by 0.02)")
    args = parser.parse_args(argv)

    try:
        bugs_by_trace = load_answer_key(args.answer_key)
    except (AnswerKeyError, OSError, json.JSONDecodeError) as error:
        parser.error(str(error))
    with open_connection(args.database_url) as conn:
        sweep_results = sweep.sweep(conn, args.thresholds) if args.sweep else None
        print(build_report(conn, bugs_by_trace, sweep_results))


if __name__ == "__main__":
    main()
