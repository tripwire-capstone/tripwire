"""Tests for the calibration report (tripwire/clusterer/report.py): answer key, scores, database reads."""

import json

import pytest

from tests.clustering_data import add_cluster, add_event, add_failed_summary, add_member, add_signal, add_summary
from tests.vectors import base_vector, vector_at
from tripwire.clusterer.report import (
    AnswerKeyError,
    LabeledEvent,
    Scores,
    bcubed,
    build_report,
    by_group,
    load_answer_key,
    load_events,
    load_funnel,
    load_nearest,
    pairwise,
    suggest_threshold,
)

TOLERANCE = 0.005


def events_in(cluster_id, bug, count, first_id=1):
    """`count` events with one bug, all in `cluster_id` (None = pool)."""
    return [LabeledEvent(first_id + index, 1, 1, cluster_id, frozenset({bug})) for index in range(count)]


def numbered(*groups):
    """Join groups of events, renumbering IDs so every event is distinct."""
    events = [event for group in groups for event in group]
    return [LabeledEvent(index + 1, event.signal_id, event.prompt_version, event.cluster_id, event.bugs)
            for index, event in enumerate(events)]


def write_answer_key(tmp_path, conversations, name="answer_key.jsonl"):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(conversation) for conversation in conversations) + "\n")
    return path


def trace_of(conn, event_id):
    """The event's Laminar trace ID as text, the way an answer key holds it."""
    return str(conn.execute("""SELECT trace.laminar_trace_id FROM signal_events sig_event
                               JOIN traces trace ON trace.id = sig_event.trace_id WHERE sig_event.id = %s""",
                            (event_id,)).fetchone()[0])


# ------------------------------------------------------------------ answer key

def test_load_answer_key_maps_each_turns_trace_to_its_bugs(tmp_path):
    path = write_answer_key(tmp_path, [{"convo_id": "convo-1", "bugs_by_turn": [
        {"turn": 1, "trace_id": "trace-a", "bugs": []},
        {"turn": 2, "trace_id": "trace-b", "bugs": ["forgetting", "large_party"]},
    ]}])
    assert load_answer_key([path]) == {"trace-a": frozenset(), "trace-b": frozenset({"forgetting", "large_party"})}


def test_load_answer_key_combines_several_runs(tmp_path):
    first = write_answer_key(tmp_path, [{"bugs_by_turn": [{"turn": 1, "trace_id": "trace-a", "bugs": ["timeout"]}]}], "one.jsonl")
    second = write_answer_key(tmp_path, [{"bugs_by_turn": [{"turn": 1, "trace_id": "trace-b", "bugs": []}]}], "two.jsonl")
    assert set(load_answer_key([first, second])) == {"trace-a", "trace-b"}


def test_load_answer_key_skips_dry_run_turns_without_a_trace(tmp_path):
    path = write_answer_key(tmp_path, [{"bugs_by_turn": [{"turn": 1, "trace_id": None, "bugs": ["timeout"]}]}])
    assert load_answer_key([path]) == {}


def test_load_answer_key_rejects_an_old_answer_key_without_bugs_by_turn(tmp_path):
    path = write_answer_key(tmp_path, [{"convo_id": "convo-1", "bugs_fired": ["forgetting"]}])
    with pytest.raises(AnswerKeyError, match="bugs_by_turn"):
        load_answer_key([path])


# ------------------------------------------------------------------ scores

def test_scores_f_beta_combines_precision_and_recall():
    scores = Scores(precision=0.8, recall=0.6)
    assert abs(scores.f1 - 0.6857) < TOLERANCE
    assert abs(scores.f_half - 0.75) < TOLERANCE            # precision counts twice
    assert Scores(precision=0.0, recall=0.0).f1 == 0.0


def test_bcubed_is_perfect_when_clusters_match_the_bugs():
    events = numbered(events_in(10, "a", 3), events_in(20, "b", 3))
    assert bcubed(events) == Scores(precision=1.0, recall=1.0)


def test_bcubed_prefers_an_honest_split_over_a_magnet_cluster_under_f_half():
    # The case behind Decision 3: three small bugs lumped together vs a big bug split in two.
    magnet = numbered(events_in(1, "big", 15), events_in(2, "b", 3), events_in(2, "c", 3), events_in(2, "d", 3))
    split = numbered(events_in(1, "big", 8), events_in(3, "big", 7),
                     events_in(4, "b", 3), events_in(5, "c", 3), events_in(6, "d", 3))
    assert abs(bcubed(magnet).precision - 0.75) < TOLERANCE and bcubed(magnet).recall == 1.0
    assert abs(bcubed(split).recall - 0.69) < TOLERANCE and bcubed(split).precision == 1.0
    assert bcubed(split).f_half > bcubed(magnet).f_half


def test_bcubed_counts_each_pool_event_as_alone():
    events = numbered(events_in(10, "a", 2), events_in(None, "a", 2))
    scores = bcubed(events)
    assert scores.precision == 1.0                     # nothing wrongly grouped
    assert abs(scores.recall - 0.375) < TOLERANCE      # (2/4 + 2/4 + 1/4 + 1/4) / 4


def test_bcubed_two_bug_event_belongs_with_either_bug():
    with_a = numbered(events_in(1, "a", 4), [LabeledEvent(0, 1, 1, 1, frozenset({"a", "b"}))], events_in(2, "b", 4))
    alone = numbered(events_in(1, "a", 4), [LabeledEvent(0, 1, 1, 3, frozenset({"a", "b"}))], events_in(2, "b", 4))
    assert bcubed(with_a).precision == 1.0             # sharing one bug is enough to belong
    assert bcubed(with_a).recall > bcubed(alone).recall


def test_bcubed_leaves_out_events_with_no_bug_in_their_turn():
    events = numbered(events_in(1, "a", 3), [LabeledEvent(0, 1, 1, 1, frozenset())])
    assert bcubed(events) == Scores(precision=1.0, recall=1.0)


def test_bcubed_uses_replacement_clusters_from_a_replay():
    events = numbered(events_in(None, "a", 2))
    assert bcubed(events).recall == 0.5
    assert bcubed(events, {1: 7, 2: 7}).recall == 1.0
    assert bcubed(events, {}).recall == 0.5            # missing from the replay = in the pool


def test_pairwise_matches_the_magnet_example():
    magnet = numbered(events_in(1, "big", 15), events_in(2, "b", 3), events_in(2, "c", 3), events_in(2, "d", 3))
    split = numbered(events_in(1, "big", 8), events_in(3, "big", 7),
                     events_in(4, "b", 3), events_in(5, "c", 3), events_in(6, "d", 3))
    assert abs(pairwise(magnet).precision - 0.81) < TOLERANCE and pairwise(magnet).recall == 1.0
    assert abs(pairwise(split).recall - 0.51) < TOLERANCE and pairwise(split).precision == 1.0


def test_by_group_splits_by_signal_and_prompt_version():
    events = [LabeledEvent(1, 1, 1, None, frozenset({"a"})), LabeledEvent(2, 1, 2, None, frozenset({"a"})),
              LabeledEvent(3, 2, 1, None, frozenset({"a"})), LabeledEvent(4, 1, 1, None, frozenset({"a"}))]
    assert {group: [event.event_id for event in members] for group, members in by_group(events).items()} == {
        (1, 1): [1, 4], (1, 2): [2], (2, 1): [3]}


def test_suggest_threshold_picks_best_f_half_and_breaks_ties_low():
    rows = [(0.20, Scores(1.0, 0.4)), (0.26, Scores(0.9, 0.8)), (0.30, Scores(0.9, 0.8)), (0.40, Scores(0.5, 1.0))]
    assert suggest_threshold(rows) == 0.26
    assert suggest_threshold([]) is None


# ------------------------------------------------------------------ database reads

def test_load_events_labels_each_event_with_its_turns_bugs(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    clustered = add_event(conn, version_id, base_vector(), session_id="convo-1")
    add_member(conn, clustered, cluster_id)
    add_summary(conn, clustered, "Refund tool timed out")
    pooled = add_event(conn, version_id, vector_at(0.5, axis=1), session_id="convo-1")
    add_event(conn, version_id, base_vector(), session_id="other-traffic")        # not in the answer key
    unembedded = add_event(conn, version_id, None, session_id="convo-1")         # never got a vector

    bugs = {trace_of(conn, clustered): frozenset({"timeout"}), trace_of(conn, pooled): frozenset(),
            trace_of(conn, unembedded): frozenset({"timeout"})}
    assert load_events(conn, bugs) == [
        LabeledEvent(clustered, signal_id, 1, cluster_id, frozenset({"timeout"})),
        LabeledEvent(pooled, signal_id, None, None, frozenset()),
    ]


def test_load_funnel_counts_only_the_answer_keys_traffic(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    clustered = add_event(conn, version_id, base_vector())
    add_member(conn, clustered, cluster_id)
    add_summary(conn, clustered, "Refund tool timed out")
    pooled = add_event(conn, version_id, vector_at(0.5, axis=1))
    failed = add_event(conn, version_id, None)
    add_failed_summary(conn, failed)
    add_event(conn, version_id, base_vector())                                   # other traffic

    bugs = {trace_of(conn, event_id): frozenset() for event_id in (clustered, pooled, failed)}
    funnel = load_funnel(conn, bugs)
    assert (funnel["traces"], funnel["runs"], funnel["events"]) == (3, 3, 3)
    assert (funnel["summaries done"], funnel["summaries failed"]) == (1, 1)
    assert (funnel["embedded"], funnel["in a cluster"], funnel["in the pool"]) == (2, 1, 1)


def test_load_nearest_finds_the_closest_event_of_the_same_signal(conn):
    _, version_id = add_signal(conn)
    _, other_version = add_signal(conn, "Signal B")
    pooled = add_event(conn, version_id, base_vector())
    near = add_event(conn, version_id, vector_at(0.20, axis=1))
    add_event(conn, version_id, vector_at(0.40, axis=2))
    add_event(conn, other_version, base_vector())                                # closer, but another Signal

    nearest = load_nearest(conn, [pooled])
    assert nearest[pooled][0] == near
    assert abs(nearest[pooled][1] - 0.20) < 1e-4


def test_build_report_shows_funnel_scores_and_bug_table(conn):
    signal_id, version_id = add_signal(conn)
    cluster_id = add_cluster(conn, signal_id, base_vector())
    event_ids = []
    for session in ("convo-1", "convo-2"):
        event_id = add_event(conn, version_id, base_vector(), session_id=session)
        add_member(conn, event_id, cluster_id)
        add_summary(conn, event_id, "Party size limit was wrong")
        event_ids.append(event_id)
    conn.execute("UPDATE clusters SET name = 'Party size limit was wrong' WHERE id = %s", (cluster_id,))

    report = build_report(conn, {trace_of(conn, event_id): frozenset({"large_party"}) for event_id in event_ids})
    assert "Funnel" in report
    assert "BCubed P 1.00 R 1.00" in report
    assert "Party size limit was wrong" in report and "large_party" in report
    assert "Sweep" not in report                                                  # only with --sweep


def test_build_report_lists_a_pool_event_with_nothing_to_compare_to(conn):
    _, version_id = add_signal(conn)
    event_id = add_event(conn, version_id, base_vector())
    report = build_report(conn, {trace_of(conn, event_id): frozenset({"timeout"})})
    assert f"event {event_id} [timeout] -> no other event of its Signal yet" in report


def test_build_report_says_when_no_events_are_embedded_yet(conn):
    _, version_id = add_signal(conn)
    event_id = add_event(conn, version_id, None)
    assert "No embedded events" in build_report(conn, {trace_of(conn, event_id): frozenset({"timeout"})})
