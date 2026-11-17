"""Tests for the cluster-naming side-by-side tool (SIG-65): Laminar matching, asking, the report."""

import pytest

from tests.clustering_data import add_cluster, add_event, add_member, add_signal, add_summary
from tests.test_llm_naming import cluster_input
from tests.vectors import base_vector, vector_at
from tripwire.clusterer.llm_client import FakeLLM
from tripwire.clusterer.llm_naming import load_naming_prompt
from tripwire.clusterer.try_naming import (
    Tried,
    ask_all,
    ask_rename,
    first_members,
    laminar_leaf_name,
    laminar_majority,
    load_clusters,
    model_stats,
    render_rename_report,
    render_report,
)
from tripwire.llm import LLMRefused, LLMUsage


# ------------------------------------------------------------------ Laminar's names

def chain(*nodes):
    return [{"id": f"c{level}", "name": name, "level": level} for level, name in nodes]


def test_laminar_leaf_is_the_lowest_named_level_above_zero():
    assert laminar_leaf_name(chain((0, None), (2, "Broad"), (1, "Specific"))) == "Specific"


def test_laminar_leaf_reads_a_json_string_and_handles_nothing_named():
    assert laminar_leaf_name('[{"id": "a", "name": "Specific", "level": 1}]') == "Specific"
    for empty in (None, [], chain((0, "Internal")), "not json"):
        assert laminar_leaf_name(empty) is None


def laminar_event(trace_id, name, signal="Issues"):
    return {"trace_id": trace_id, "signal_name": signal, "cluster_details": chain((1, name))}


def test_laminar_majority_counts_each_trace_once_for_the_matching_signal():
    events = [laminar_event("t1", "Refund Delays"), laminar_event("t1", "Something Else"),
              laminar_event("t2", "Refund Delays"), laminar_event("t3", "Wrong Size"),
              laminar_event("t4", "Refund Delays", signal="Intent"),      # other Signal
              laminar_event("t9", "Refund Delays")]                       # not our trace
    assert laminar_majority({"t1", "t2", "t3", "t4"}, events, "issues") == (
        "Refund Delays (2 of 3 traces)")


def test_laminar_majority_is_none_when_laminar_named_none_of_the_traces():
    assert laminar_majority({"t1"}, [laminar_event("t2", "A")], "Issues") is None


def test_laminar_majority_breaks_a_tie_the_same_way_every_time():
    events = [laminar_event("t1", "Beta"), laminar_event("t2", "Alpha")]
    assert laminar_majority({"t1", "t2"}, events, "Issues") == "Beta (1 of 2 traces)"
    assert laminar_majority({"t1", "t2"}, list(reversed(events)), "Issues") == "Beta (1 of 2 traces)"


# ------------------------------------------------------------------ asking

def tried(cluster_id=1, signal="Issues"):
    return Tried(cluster_id, signal, 3, "Today's name", ["summary 1"])


def test_ask_all_records_each_models_name_and_a_models_llm_error():
    llm = FakeLLM([{"name": "Refund Delays"}, LLMRefused("no")])
    result = ask_all(llm, ["model-a", "model-b"], load_naming_prompt(), cluster_input(), tried())
    assert result.answers["model-a"]["name"] == "Refund Delays"
    assert result.answers["model-a"]["input_tokens"] == llm.usage.input_tokens
    assert result.answers["model-b"] == {"error": "LLMRefused: no"}
    assert [r["model"] for r in llm.requests] == ["model-a", "model-b"]


def test_ask_all_stops_loudly_on_anything_but_an_llm_error():
    with pytest.raises(ValueError):
        ask_all(FakeLLM([ValueError("bad model name")]), ["model-a"], load_naming_prompt(),
                cluster_input(), tried())


def test_first_members_keeps_only_those_members_in_their_order():
    cut = first_members(cluster_input(5), {2, 4})
    assert [m.event_id for m in cut.members] == [2, 4] and cut.member_count == 2


# ------------------------------------------------------------------ report

def answered(name, seconds=1.0, tokens=(1000, 10)):
    return {"name": name, "seconds": seconds, "input_tokens": tokens[0], "output_tokens": tokens[1]}


def test_model_stats_prices_known_models_and_counts_errors():
    first, second = tried(1), tried(2)
    first.answers = {"gpt-6.1-sol": answered("Refund Delays", 2.0, (1_000_000, 0)),
                     "mystery": answered("A B C")}
    second.answers = {"gpt-6.1-sol": {"error": "LLMRefused: no"}, "mystery": answered("A B C D E")}
    sol = model_stats([first, second], "gpt-6.1-sol")
    assert (sol["named"], sol["errors"], sol["avg_words"], sol["cost_usd"]) == (1, 1, 2.0, 2.0)
    mystery = model_stats([first, second], "mystery")
    assert (mystery["avg_words"], mystery["cost_usd"]) == (4.0, None)     # not priced, not 0


def test_report_has_a_row_per_cluster_with_every_model_and_laminar():
    row = tried(7)
    row.laminar = "Refund Delays (2 of 3 traces)"
    row.answers = {"m1": answered("Refund Delays"), "m2": {"error": "LLMRefused: no"}}
    report = render_report([row], ["m1", "m2"], {"taken_at": "now", "prompt_version": 1,
                                                   "as_of": None, "laminar": True})
    assert "| 7 | 3 | Today's name | Refund Delays | **error** | Refund Delays (2 of 3 traces) |" in report
    assert "- m2: LLMRefused: no" in report


def test_report_escapes_pipes_in_names():
    row = tried(7)
    row.answers = {"m1": answered("Returns | Exchanges")}
    report = render_report([row], ["m1"], {"taken_at": "now", "prompt_version": 1, "as_of": 5,
                                            "laminar": False})
    assert "Returns \\| Exchanges" in report and "first 5 members" in report


# ------------------------------------------------------------------ load_clusters (database)

def test_load_clusters_skips_clusters_with_nothing_summarized_and_cuts_to_the_first_n(conn):
    signal_id, version_id = add_signal(conn, name="Issues")
    cluster_id = add_cluster(conn, signal_id, base_vector())
    joined = []
    for index in range(4):
        event_id = add_event(conn, version_id, vector_at(0.1, index + 1))
        add_member(conn, event_id, cluster_id)
        add_summary(conn, event_id, f"summary {index}")
        joined.append(event_id)
    # The two newest events joined first, so "first N" can't be confused with "lowest IDs".
    for minutes_ago, event_id in zip([5, 4, 20, 10], joined):
        conn.execute("UPDATE cluster_members SET assigned_at = now() - make_interval(mins => %s) "
                     "WHERE event_id = %s", (minutes_ago, event_id))
    empty = add_cluster(conn, signal_id, base_vector())
    add_member(conn, add_event(conn, version_id, vector_at(0.1, 9)), empty)   # no summary

    loaded, earliest = load_clusters(conn, signal_filter=None, as_of=None)
    assert [t.cluster_id for t, _, _, _ in loaded] == [cluster_id]
    tried_row, cluster, traces, _ = loaded[0]
    assert (tried_row.size, len(traces), earliest is not None) == (4, 4, True)

    first_two = [joined[2], joined[3]]
    [(tried_row, cluster, traces, _)] = load_clusters(conn, signal_filter="issues", as_of=2)[0]
    assert sorted(m.event_id for m in cluster.members) == sorted(first_two)
    assert (tried_row.size, len(traces)) == (2, 2)
    assert load_clusters(conn, signal_filter="Intent", as_of=None)[0] == []


# ------------------------------------------------------------------ --rename-from

def test_ask_rename_names_small_then_renames_with_that_name_shown_then_names_fresh():
    llm = FakeLLM([{"name": "Small Name"}, {"name": "Small Name"}, {"name": "Fresh Name"}])
    full = cluster_input(10)
    result = ask_rename(llm, ["model-a"], load_naming_prompt(), full, {1, 2, 3}, tried())
    small_call, rename_call, fresh_call = (r["messages"][1]["content"] for r in llm.requests)
    assert "size: 3 findings" in small_call and "current name" not in small_call
    assert "size: 10 findings" in rename_call and "current name: Small Name" in rename_call
    assert "size: 10 findings" in fresh_call and "current name" not in fresh_call
    answer = result.answers["model-a"]
    assert (answer["at_n"], answer["renamed"], answer["fresh"], answer["kept"]) == (
        "Small Name", "Small Name", "Fresh Name", True)
    assert answer["input_tokens"] == 3 * llm.usage.input_tokens


def test_ask_rename_records_a_changed_name_and_an_error():
    llm = FakeLLM([{"name": "Small"}, {"name": "Broader"}, {"name": "Fresh"}, LLMRefused("no")])
    result = ask_rename(llm, ["model-a", "model-b"], load_naming_prompt(), cluster_input(10),
                        {1, 2, 3}, tried())
    assert result.answers["model-a"]["kept"] is False
    assert result.answers["model-b"] == {"error": "LLMRefused: no"}


def test_rename_report_counts_kept_and_changed_names():
    first, second = tried(1), tried(2)
    first.answers = {"m1": {"at_n": "A", "renamed": "A", "fresh": "B", "kept": True, "seconds": 1,
                            "input_tokens": 3, "output_tokens": 1}}
    second.answers = {"m1": {"at_n": "C", "renamed": "D", "fresh": "D", "kept": False, "seconds": 1,
                             "input_tokens": 3, "output_tokens": 1}}
    report = render_rename_report([first, second], ["m1"], {"taken_at": "now", "prompt_version": 1,
                                                            "rename_from": 3})
    assert "| m1 | 2 | 1 | 1 | 0 |" in report
    assert "| 1 | 3 | m1 | A | A | kept | B |" in report
    assert "| 2 | 3 | m1 | C | D | changed | D |" in report


def test_rename_from_takes_only_clusters_the_clusterer_would_rename(conn):
    signal_id, version_id = add_signal(conn, name="Issues")
    sizes = {}
    for size in (5, 6):
        cluster_id = add_cluster(conn, signal_id, base_vector())
        for index in range(size):
            event_id = add_event(conn, version_id, vector_at(0.1, index + 1))
            add_member(conn, event_id, cluster_id)
            add_summary(conn, event_id, f"summary {index}")
        sizes[cluster_id] = size
    loaded, _ = load_clusters(conn, signal_filter=None, as_of=None, rename_from=3)
    [(tried_row, cluster, _, first_ids)] = loaded
    assert sizes[tried_row.cluster_id] == 6                      # 5 < 2 x 3: not renamed yet
    assert len(first_ids) == 3 and cluster.member_count == 6
