"""Metrics A-C on small hand-checkable cases (no database)."""

import pytest

from scorer import data, metrics
from scorer.data import Event


def lm_event(event_id, trace, details):
    return Event(event_id, trace, data.laminar_clusters(details))


# ------------------------------------------------------------------ which Laminar cluster

def test_level_0_only_is_unclustered():
    assert data.laminar_clusters([{"id": "z", "name": "", "level": 0}]) == ()


def test_lowest_named_level_wins_and_empty_names_are_skipped():
    details = [{"id": "p", "name": "Parent theme", "level": 2}, {"id": "x", "name": "", "level": 1},
               {"id": "c", "name": "Child", "level": 1}, {"id": "z", "name": "", "level": 0}]
    assert data.laminar_clusters(details) == ("c",)


def test_two_named_clusters_at_the_lowest_level_are_both_kept():
    details = [{"id": "b", "name": "Second", "level": 1}, {"id": "a", "name": "First", "level": 1},
               {"id": "p", "name": "Parent", "level": 2}]
    assert data.laminar_clusters(details) == ("a", "b")


def test_single_is_the_lowest_str_so_10_beats_9():
    assert Event(1, "t", (9, 10)).single == 10
    assert Event(1, "t", ()).single is None


# ------------------------------------------------------------------ A. answer key

def test_answer_key_leaves_out_unlabeled_and_counts_unclustered_alone():
    events = [Event(1, "t1", ("c",)), Event(2, "t2", ("c",)), Event(3, "t3", ()), Event(4, "t4", ("c",))]
    labels = {"t1": frozenset({"timeout"}), "t2": frozenset({"timeout"}), "t3": frozenset({"timeout"})}
    scores = metrics.answer_key_scores(events, labels)          # t4 has no label: left out
    # P: events 1, 2 have mates {1, 2} both timeout -> 1; event 3 alone -> 1.  P = 1.
    # R: events 1, 2 find 2 of 3 timeout events in their cluster; event 3 finds 1 of 3. R = (2/3+2/3+1/3)/3
    assert scores["precision"] == pytest.approx(1.0)
    assert scores["recall"] == pytest.approx(5 / 9)
    assert (scores["n_labeled"], scores["n_events"], scores["pct_unclustered"]) == (3, 4, 25.0)


def test_signals_match_case_insensitively_and_intent_is_counts_only():
    tripwire, laminar = data.SystemView(data.TRIPWIRE), data.SystemView(data.LAMINAR)
    tripwire.add("User frustration", Event(1, "t1", (5,)))
    tripwire.add("Intent", Event(2, "t1", ()))
    laminar.add("User Frustration", Event("a", "t1", ("L",)))
    laminar.add("Intent", Event("b", "t1", ()))
    keys = {"turn": {}, "sofar": {"t1": frozenset({"decoy"})}}
    out = metrics.score_signals(tripwire, laminar, keys)
    assert out["user frustration"]["label_mode"] == "sofar"
    assert out["user frustration"]["agreement"]["n_shared"] == 1
    assert out["user frustration"]["answer_key"]["laminar"]["n_labeled"] == 1
    assert out["intent"]["answer_key"] is None


# ------------------------------------------------------------------ B. agreement

def test_tripwire_trace_cluster_is_most_common_ties_keep_every_tied_cluster():
    events = [Event(1, "t1", (9,)), Event(2, "t1", (10,)),             # tie: both
              Event(3, "t2", (4,)), Event(4, "t2", (4,)), Event(5, "t2", (7,)),
              Event(6, "t3", ()), Event(7, "t3", (5,)),                # unclustered events don't vote
              Event(8, "t4", ())]
    assert metrics.tripwire_trace_clusters(events) == {
        "t1": frozenset({9, 10}), "t2": frozenset({4}), "t3": frozenset({5}), "t4": frozenset()}


def test_bcubed_multi_equals_report_bcubed_with_one_cluster_per_item():
    import random

    from tripwire.clusterer.report import LabeledEvent, bcubed
    rng = random.Random(7)
    for _ in range(50):
        items = []
        for _ in range(rng.randint(1, 12)):
            cluster = rng.choice([None, "A", "B", "C"])
            labels = frozenset(rng.sample(["x", "y", "z"], rng.randint(0, 2)))
            items.append((cluster, labels))
        reference = bcubed([LabeledEvent(index, 0, None, cluster, labels)
                            for index, (cluster, labels) in enumerate(items)])
        extended = metrics.bcubed_multi([(frozenset() if cluster is None else frozenset({cluster}), labels)
                                         for cluster, labels in items])
        assert extended.precision == pytest.approx(reference.precision)
        assert extended.recall == pytest.approx(reference.recall)


def test_bcubed_multi_by_hand_with_a_trace_in_two_clusters():
    # t1 in A and B, label L1; t2 in A, label L1; t3 in B, label L2.
    items = [(frozenset({"A", "B"}), frozenset({"L1"})), (frozenset({"A"}), frozenset({"L1"})),
             (frozenset({"B"}), frozenset({"L2"}))]
    scores = metrics.bcubed_multi(items)
    # P: t1 shares clusters with t1 (|C|=2, |L|=1 -> 1/2), t2 (1 of 1 -> 1), t3 (1, L none -> 0): 1/2
    #    t2 with t1 (1/1), t2 (1/1): 1.   t3 with t1 (0/1), t3 (1/1): 1/2.   P = (1/2 + 1 + 1/2) / 3 = 2/3
    # R: t1 with t1 (min(2,1)/1 = 1), t2 (1/1): 1.   t2 with t1, t2: 1.   t3 with t3: 1.   R = 1
    assert scores.precision == pytest.approx(2 / 3)
    assert scores.recall == pytest.approx(1.0)


def test_agreement_counts_a_tied_tripwire_trace_in_both_clusters():
    tripwire = [Event(1, "t1", ("A",)), Event(2, "t1", ("B",)), Event(3, "t2", ("A",)), Event(4, "t3", ("B",))]
    laminar = [Event("a", "t1", ("L1",)), Event("b", "t2", ("L1",)), Event("c", "t3", ("L2",))]
    result = metrics.agreement(tripwire, laminar)
    assert result["n_tripwire_tied"] == 1
    assert result["precision"] == pytest.approx(2 / 3) and result["recall"] == pytest.approx(1.0)
    rows = {row["laminar_cluster"]: row for row in result["mapping"]}
    assert rows["L1"]["n_traces"] == 2 and (rows["L1"]["tripwire_cluster"], rows["L1"]["n_shared"]) == ("A", 2)


def test_laminar_trace_keeps_both_clusters_of_a_two_cluster_event():
    events = [Event("a", "t1", ("L1", "L2"))]
    assert metrics.laminar_trace_labels(events) == {"t1": frozenset({"L1", "L2"})}


def test_laminar_tie_between_separate_events_goes_to_lowest_str():
    events = [Event("a", "t1", ("L2",)), Event("b", "t1", ("L1",)), Event("c", "t2", ())]
    assert metrics.laminar_trace_labels(events) == {"t1": frozenset({"L1"}), "t2": frozenset()}


def test_agreement_by_hand():
    # Laminar: t1, t2, t3 in L1; t4 in L2; t5 shared but Laminar-unclustered (not scored);
    # t6 only Laminar (not shared). Tripwire: t1, t2 in A; t3 unclustered; t4 in A.
    tripwire = [Event(1, "t1", ("A",)), Event(2, "t2", ("A",)), Event(3, "t3", ()), Event(4, "t4", ("A",)),
                Event(5, "t5", ("A",))]
    laminar = [Event("a", "t1", ("L1",)), Event("b", "t2", ("L1",)), Event("c", "t3", ("L1",)),
               Event("d", "t4", ("L2",)), Event("e", "t5", ()), Event("f", "t6", ("L1",))]
    result = metrics.agreement(tripwire, laminar)
    assert (result["n_shared"], result["n_scored"], result["n_tripwire_unclustered"]) == (5, 4, 1)
    # P: t1, t2 -> 2/3 (A holds t1, t2, t4); t3 alone -> 1; t4 -> 1/3.  (2/3+2/3+1+1/3)/4 = 2/3
    # R: t1, t2 -> 2/3 of L1 in A; t3 -> 1/3; t4 -> 1/1.  (2/3+2/3+1/3+1)/4 = 2/3
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["recall"] == pytest.approx(2 / 3)
    rows = {row["laminar_cluster"]: row for row in result["mapping"]}
    assert rows["L1"] == {"laminar_cluster": "L1", "n_traces": 3, "tripwire_cluster": "A", "n_shared": 2}
    assert rows["L2"]["tripwire_cluster"] == "A"


def test_mapping_can_point_at_unclustered():
    tripwire = [Event(1, "t1", ()), Event(2, "t2", ()), Event(3, "t3", ("A",))]
    laminar = [Event("a", "t1", ("L",)), Event("b", "t2", ("L",)), Event("c", "t3", ("L",))]
    row = metrics.agreement(tripwire, laminar)["mapping"][0]
    assert (row["tripwire_cluster"], row["n_shared"], row["n_traces"]) == ("unclustered", 2, 3)


def test_a_two_cluster_trace_counts_toward_both_laminar_clusters_in_the_mapping():
    tripwire = [Event(1, "t1", ("A",))]
    laminar = [Event("a", "t1", ("L1", "L2"))]
    result = metrics.agreement(tripwire, laminar)
    assert sorted(row["laminar_cluster"] for row in result["mapping"]) == ["L1", "L2"]
    assert result["precision"] == pytest.approx(1.0)


def test_agreement_with_nothing_scored_is_none_not_a_crash():
    result = metrics.agreement([Event(1, "t1", ("A",))], [Event("a", "t1", ())])
    assert result["n_scored"] == 0 and result["precision"] is None


# ------------------------------------------------------------------ C. coverage

def test_coverage_counts_and_largest_share():
    events = [Event(1, "t1", ("A",)), Event(2, "t2", ("A",)), Event(3, "t3", ("B",)), Event(4, "t4", ())]
    assert metrics.coverage(events) == {"n_events": 4, "n_clusters": 2, "n_unclustered": 1,
                                        "pct_unclustered": 25.0, "largest_share": 0.5}


def test_coverage_of_no_events():
    assert metrics.coverage([])["pct_unclustered"] == 0.0
