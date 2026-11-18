"""`score check`: recompute the spec's hand-computed numbers (scorer-spec.md, "Expected numbers")
and print expected vs computed. Proportions are compared rounded to 2 decimals, counts exactly.
The expected values are copied from the spec verbatim and never edited to fit.

ACCEPTED lists the rows the user reviewed and accepted on Nov 18 (they differ from the spec's hand
numbers for the reasons given). Such a row passes only while the computed value equals the
accepted one, so it still works as a regression lock. Exit status: non-zero if any row matches
neither the spec nor its accepted value.
"""

from . import data, metrics, runner

_LAMINAR_PICK = ("A Laminar event in two clusters takes the lowest id (decided Nov 18); the hand "
                 "numbers took the first cluster in Laminar's list, which reproduces 0.93 / 0.28 / 0.63.")
_TIES = ("Two-cluster Laminar events count for both and tied Tripwire traces count in every tied "
         "cluster (decided Nov 18). No reading tried (lowest id, list order, numeric tie-break, "
         "unclustered grouped or skipped) reproduces the hand number; accepted as computed.")
_MAPPING = ("Traces in two Laminar clusters, and tied Tripwire traces, count toward both "
            "clusters (decided Nov 18), adding 3 traces here.")

# (section, item): (accepted computed value, reason). Accepted by the user on Nov 18.
ACCEPTED = {
    ("answer key, live", "park2 issues laminar P"): (0.92, _LAMINAR_PICK),
    ("answer key, live", "park2 issues laminar R"): (0.26, _LAMINAR_PICK),
    ("answer key, live", "park2 issues laminar F0.5"): (0.61, _LAMINAR_PICK),
    ("agreement, live", "park2 issues P"): (0.85, _TIES),
    ("agreement, live", "park4 issues P"): (0.78, _TIES),
    ("agreement, live", "park4 issues R"): (0.40, _TIES),
    ("agreement, live", "tau1 issues P"): (0.90, _TIES),
    ("agreement, live", "tau1 issues R"): (0.54, _TIES),
    ("tau1 Issues mapping", "Single-Turn Multiple Tool Call Policy Violations scored traces"): (27, _MAPPING),
    ("tau1 Issues mapping", "Single-Turn Multiple Tool Call Policy Violations -> count"): (24, _MAPPING),
}

# (run, signal key, system): (P, R, F0.5), live clusters from raw.json
ANSWER_KEY_LIVE = {
    ("park2", "issues", "tripwire"): (0.95, 0.59, 0.85),
    ("park2", "issues", "laminar"): (0.93, 0.28, 0.63),
    ("park2", "user frustration", "tripwire"): (0.96, 0.47, 0.80),
    ("park2", "user frustration", "laminar"): (1.00, 0.07, 0.27),
    ("park4", "issues", "tripwire"): (0.89, 0.46, 0.75),
    ("park4", "issues", "laminar"): (0.88, 0.47, 0.75),
    ("park4", "user frustration", "tripwire"): (0.94, 0.52, 0.81),
    ("park4", "user frustration", "laminar"): (0.94, 0.14, 0.44),
}

# (run, signal key): F0.5 of the Tripwire replay at 0.24
REPLAY_F05 = {
    ("park2", "issues"): 0.85, ("park2", "user frustration"): 0.81,
    ("park4", "issues"): 0.75, ("park4", "user frustration"): 0.82,
}
REPLAY_THRESHOLD = 0.24

# (run, signal key): (shared, scored, TW-unclustered among scored, P, R)
AGREEMENT = {
    ("park2", "issues"): (192, 73, 10, 0.95, 0.57),
    ("park2", "user frustration"): (163, 24, 1, 1.00, 0.62),
    ("park4", "issues"): (168, 83, 5, 0.81, 0.41),
    ("park4", "user frustration"): (167, 51, 9, 0.93, 0.58),
    ("tau1", "issues"): (89, 56, 15, 0.85, 0.48),
}

# tau1 Issues mapping: Laminar name -> (scored traces, Tripwire name or "unclustered", count)
TAU1_ISSUES_MAPPING = {
    "Single-Turn Multiple Tool Call Policy Violations": (24, "Concurrent Tool Calls Despite Sequential Requirement", 21),
    "Simultaneous Message and Tool Call Violations": (15, "Identity Lookups With Fabricated Personal Details", 9),
    "Agent Output JSON Format Violation": (17, "unclustered", 9),
}

# tau1 coverage, events: (signal key, system): (unclustered, events)
TAU1_COVERAGE = {
    ("issues", "tripwire"): (99, 179), ("issues", "laminar"): (42, 107),
    ("user frustration", "tripwire"): (23, 26), ("user frustration", "laminar"): (109, 116),
    ("intent", "tripwire"): (160, 220), ("intent", "laminar"): (195, 220),
}


def _same(expected, computed):
    if computed is None:
        return False
    if isinstance(expected, int):
        return expected == computed
    return round(expected, 2) == round(computed, 2)


def _fmt(value):
    if value is None:
        return "-"
    return str(value) if isinstance(value, int) else f"{value:.2f}"


def compute(runs_by_id, log=print):
    """Rows of (section, item, expected, computed)."""
    rows = []
    live = {}
    for run_id in ("park2", "park4", "tau1"):
        run = runs_by_id[run_id]
        raw = data.load_raw(run.laminar_raw)
        tripwire, laminar = data.tripwire_from_raw(raw), data.laminar_from_raw(raw)
        live[run_id] = (metrics.score_signals(tripwire, laminar, runner.load_answer_keys(run)), tripwire, laminar)

    for (run_id, key, system), expected in ANSWER_KEY_LIVE.items():
        scores = live[run_id][0][key]["answer_key"][system]
        for name, value, got in zip(("P", "R", "F0.5"), expected, (scores["precision"], scores["recall"], scores["f05"])):
            rows.append(("answer key, live", f"{run_id} {key} {system} {name}", value, got))

    from .database import connect
    for run_id in ("park2", "park4"):
        run = runs_by_id[run_id]
        log(f"check: replaying {run_id} at {REPLAY_THRESHOLD} ...")
        with connect(run.db) as conn:
            membership, names, _ = runner.replay_run(conn, REPLAY_THRESHOLD)
            replayed = data.tripwire_from_db(conn, membership=membership, names=names)
        scored = metrics.score_signals(replayed, None, runner.load_answer_keys(run))
        for (expected_run, key), value in REPLAY_F05.items():
            if expected_run == run_id:
                rows.append(("answer key, replay 0.24", f"{run_id} {key} TW F0.5", value,
                             scored[key]["answer_key"]["tripwire"]["f05"]))

    for (run_id, key), expected in AGREEMENT.items():
        agreement = live[run_id][0][key]["agreement"]
        got = (agreement["n_shared"], agreement["n_scored"], agreement["n_tripwire_unclustered"],
               agreement["precision"], agreement["recall"])
        for name, value, computed in zip(("shared", "scored", "TW-unclustered", "P", "R"), expected, got):
            rows.append(("agreement, live", f"{run_id} {key} {name}", value, computed))

    _, tripwire, laminar = live["tau1"]
    mapping = {row["laminar_cluster"]: row for row in live["tau1"][0]["issues"]["agreement"]["mapping"]}
    by_name = {laminar.cluster_names.get(cluster_id): row for cluster_id, row in mapping.items()}
    for laminar_name, (n_traces, tripwire_name, n_shared) in TAU1_ISSUES_MAPPING.items():
        row = by_name.get(laminar_name)
        got_name = None
        if row is not None:
            got_name = ("unclustered" if row["tripwire_cluster"] == metrics.UNCLUSTERED
                        else tripwire.cluster_names.get(row["tripwire_cluster"]))
        rows.append(("tau1 Issues mapping", f"{laminar_name} scored traces", n_traces, row and row["n_traces"]))
        rows.append(("tau1 Issues mapping", f"{laminar_name} -> Tripwire cluster", tripwire_name, got_name))
        rows.append(("tau1 Issues mapping", f"{laminar_name} -> count", n_shared, row and row["n_shared"]))

    for (key, system), (unclustered, events) in TAU1_COVERAGE.items():
        coverage = live["tau1"][0][key]["coverage"][system]
        rows.append(("tau1 coverage", f"{key} {system} unclustered", unclustered, coverage["n_unclustered"]))
        rows.append(("tau1 coverage", f"{key} {system} events", events, coverage["n_events"]))
    return rows


def _matches(expected, computed):
    return (expected == computed) if isinstance(expected, str) else _same(expected, computed)


def status(section, item, expected, computed, accepted=None):
    """"ok" (matches the spec), "accepted" (matches its accepted value), "spec-now" (an accepted
    row that matches the spec again: drop its acceptance), or "MISMATCH"."""
    accepted = ACCEPTED if accepted is None else accepted
    if _matches(expected, computed):
        return "spec-now" if (section, item) in accepted else "ok"
    if (section, item) in accepted and _matches(accepted[(section, item)][0], computed):
        return "accepted"
    return "MISMATCH"


def render(rows, accepted=None):
    """(text, failures). Only MISMATCH rows fail; accepted rows show their reason once."""
    accepted = ACCEPTED if accepted is None else accepted
    width = max(len(item) for _, item, _, _ in rows)
    lines, section, counts, reasons = [], None, {}, []
    for this_section, item, expected, computed in rows:
        if this_section != section:
            section = this_section
            lines.append(f"\n{section}")
        state = status(this_section, item, expected, computed, accepted)
        counts[state] = counts.get(state, 0) + 1
        shown_expected = expected if isinstance(expected, str) else _fmt(expected)
        shown_computed = computed if isinstance(computed, str) or computed is None else _fmt(computed)
        note = ""
        if state == "accepted":
            reason = accepted[(this_section, item)][1]
            if reason not in reasons:
                reasons.append(reason)
            note = f"  [accepted, note {reasons.index(reason) + 1}]"
        elif state == "MISMATCH" and (this_section, item) in accepted:
            note = f"  [accepted value was {_fmt(accepted[(this_section, item)][0])}]"
        label = {"ok": "ok ", "accepted": "accepted", "spec-now": "ok (spec; drop acceptance)",
                 "MISMATCH": "MISMATCH"}[state]
        lines.append(f"  {label:<8} {item:<{width}}  expected {shown_expected!s:>8}  "
                     f"computed {shown_computed!s:>8}{note}")
    if reasons:
        lines.append("\nNotes on accepted rows (accepted by the user, Nov 18):")
        lines += [f"  {index}. {reason}" for index, reason in enumerate(reasons, start=1)]
    failures = counts.get("MISMATCH", 0)
    lines.append(f"\n{counts.get('ok', 0) + counts.get('spec-now', 0)}/{len(rows)} match the spec, "
                 f"{counts.get('accepted', 0)} accepted, {failures} mismatch(es)")
    return "\n".join(lines), failures
