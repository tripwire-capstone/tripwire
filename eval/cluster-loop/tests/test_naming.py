"""Metric D pieces with fakes (no paid calls)."""

import pytest

from scorer import naming
from scorer.data import Event
from tripwire.clusterer.embed import FakeEmbedder
from tripwire.llm import LLMUsage
from tripwire.clusterer.llm_client import LLMResult


class FakeGrader:
    def __init__(self, output):
        self.output, self.messages = output, []

    def complete(self, model, messages, json_schema):
        self.messages.append(messages)
        return LLMResult(output=self.output, usage=LLMUsage(cost_usd=0.001))


def events(cluster, trace_labels):
    return [Event(index, trace, (cluster,)) for index, trace in enumerate(trace_labels)]


def test_keyword_check_scores_only_clusters_with_a_majority_cause():
    labels = {"a": frozenset({"timeout"}), "b": frozenset({"timeout"}), "c": frozenset({"large_party"}),
              "d": frozenset({"glacier_mixup"}), "e": frozenset({"date_shift"}), "f": frozenset({"decoy"})}
    by_cluster = {1: events(1, ["a", "b", "c"]),          # timeout 2/3: qualifies
                  2: events(2, ["d", "e", "f"]),          # 1/3 each: no majority, left out
                  3: events(3, ["c"])}                     # large_party 1/1
    names = {1: "Park Lookups TIMED OUT", 2: "Mixed", 3: "Wrong park shown"}
    result = naming.keyword_check(by_cluster, names, labels)
    assert result["n_clusters"] == 2
    assert [(row["cluster"], row["match"]) for row in result["rows"]] == [(1, True), (3, False)]
    assert result["score"] == 0.5


def test_majority_is_at_least_half_and_ignores_unlabeled_events():
    labels = {"a": frozenset({"timeout"}), "b": frozenset({"date_shift"})}
    cause, share = naming.majority_cause(events(1, ["a", "b", "unlabeled"]), labels)
    assert (cause, share) == ("date_shift", 0.5)            # tie at 50%: lowest name


def test_keyword_check_with_no_qualifying_cluster_is_none():
    assert naming.keyword_check({}, {}, {})["score"] is None


def test_rubric_grade_is_mean_of_sum_over_6_and_shows_members_and_siblings():
    cluster = naming.NamedCluster(1, 1, "Issues", "Timeouts", "llm", ["central one"], ["far one"], ["Other"])
    grader = FakeGrader({"cause": 2, "fit": 1, "distinct": 2})
    result = naming.rubric_grade([cluster], grader, "grader-model")
    assert result["score"] == pytest.approx(5 / 6)
    prompt = grader.messages[0][1]["content"]
    assert "Timeouts" in prompt and "central one" in prompt and "far one" in prompt and "Other" in prompt


def test_rubric_grade_rejects_an_out_of_range_answer():
    cluster = naming.NamedCluster(1, 1, "Issues", "X", "llm", [], [], [])
    with pytest.raises(ValueError):
        naming.rubric_grade([cluster], FakeGrader({"cause": 3, "fit": 1, "distinct": 1}), "m")


def test_laminar_match_uses_pairs_with_at_least_3_shared_traces():
    mapping = [{"laminar_cluster": "L1", "n_traces": 5, "tripwire_cluster": 1, "n_shared": 3},
               {"laminar_cluster": "L2", "n_traces": 5, "tripwire_cluster": 2, "n_shared": 2},
               {"laminar_cluster": "L3", "n_traces": 5, "tripwire_cluster": "unclustered", "n_shared": 4}]
    embedder = FakeEmbedder()
    result = naming.laminar_match(mapping, {1: "Same name", 2: "B"}, {"L1": "Same name", "L2": "B"}, embedder)
    assert result["n_pairs"] == 1 and result["score"] == pytest.approx(1.0)
    assert embedder.calls == [["Same name", "Same name"]]


def test_cost_counter_adds_up_usage():
    counter = naming.CostCounter(FakeGrader({"cause": 0, "fit": 0, "distinct": 0}))
    counter.complete("m", [], {})
    counter.complete("m", [], {})
    assert counter.calls == 2 and counter.cost_usd == pytest.approx(0.002)
