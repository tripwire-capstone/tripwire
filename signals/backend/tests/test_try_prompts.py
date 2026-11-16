"""Tests for the prompt side-by-side tool (SIG-64): sampling, asking each version, the report."""

from contextlib import nullcontext

from tests.test_summarize import GOOD, make_event
from tripwire.clusterer import loop
from tripwire.clusterer.llm_client import FakeLLM
from tripwire.clusterer.summarize import load_prompt
from tripwire.clusterer.try_prompts import Sampled, ask, pick_sample, render_report, version_stats
from tripwire.llm import LLMRefused


def row(event_id, signal="Issues", cluster_id=None, cluster_name=None, description="the judge's text"):
    return (event_id, signal, cluster_id, cluster_name, 1, f"stored summary {event_id}", description, {})


def test_sample_takes_a_few_per_cluster_and_a_few_unclustered_per_signal():
    rows = ([row(n, cluster_id=1, cluster_name="A") for n in range(1, 6)]
            + [row(n, cluster_id=2, cluster_name="B") for n in range(6, 8)]
            + [row(n) for n in range(8, 13)]
            + [row(n, signal="Intent") for n in range(13, 15)])
    sample = pick_sample(rows, per_cluster=2, unclustered=3, seed=1)
    by_group = {}
    for event in sample:
        by_group.setdefault((event.signal, event.cluster_id), []).append(event.event_id)
    assert {group: len(ids) for group, ids in by_group.items()} == {
        ("Intent", None): 2, ("Issues", 1): 2, ("Issues", 2): 2, ("Issues", None): 3}
    assert [e.signal for e in sample] == sorted(e.signal for e in sample)     # grouped, stable order


def test_same_seed_gives_the_same_sample():
    rows = [row(n, cluster_id=1, cluster_name="A") for n in range(1, 20)]
    first = [e.event_id for e in pick_sample(rows, 3, 3, seed=7)]
    assert first == [e.event_id for e in pick_sample(rows, 3, 3, seed=7)]


def test_finding_falls_back_to_the_payload_when_there_is_no_description():
    rows = [(1, "Issues", None, None, 1, "s", None, {"reason": "skipped confirmation"})]
    assert "skipped confirmation" in pick_sample(rows, 2, 3, 1)[0].finding


def test_ask_records_each_versions_answer_and_an_error_without_stopping(conn, monkeypatch):
    monkeypatch.setattr(loop, "open_connection", lambda url: nullcontext(conn))
    _, _, event_id = make_event(conn)
    event = Sampled(event_id, "Signal A", None, None, 1, "stored", "finding")
    llm = FakeLLM([GOOD, LLMRefused("no"), GOOD | {"details": [" park: Glacier Bay ", ""]}])
    prompts = {1: load_prompt(1), 2: load_prompt(2), 3: load_prompt(3)}

    ask("unused-url", llm, "gpt-6-luna", prompts, event)

    assert event.answers[1]["summary"] == GOOD["summary"] and event.answers[1]["details"] is None
    assert "LLMRefused" in event.answers[2]["error"]
    assert event.answers[3]["details"] == ["park: Glacier Bay"]          # cleaned like the real path
    assert [r["json_schema"] for r in llm.requests] == [p.schema for p in prompts.values()]


def test_report_marks_the_rerun_and_shows_details_errors_and_stats():
    event = Sampled(5, "Issues", 3, "JSON replies", 1, "stored text", "finding text")
    event.answers = {1: {"summary": "rerun text", "details": None, "cost_usd": 0.001},
                     2: {"summary": "v2 text", "details": [], "cost_usd": 0.001},
                     3: {"error": "LLMRefused: no"}}
    report = render_report([event], [1, 2, 3], {"taken_at": "now", "model": "m", "seed": 1})
    assert "## Issues · cluster 3: JSON replies" in report
    assert "- stored v1: stored text" in report
    assert "- v1 (re-run): rerun text" in report
    assert "- v2: v2 text · details: (none)" in report
    assert "- v3: **error** LLMRefused: no" in report
    assert version_stats([event], 2)["events_with_no_details"] == 1
    assert version_stats([event], 3)["errors"] == 1


def test_the_sample_query_reads_only_finished_summaries_with_their_cluster(conn):
    from datetime import datetime, timezone

    from tests.test_summarize import ok
    from tripwire.clusterer.summarize import decide, save
    from tripwire.clusterer.try_prompts import SAMPLE_SQL
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    _, _, event_id = make_event(conn)
    assert conn.execute(SAMPLE_SQL).fetchall() == []                    # not summarized yet
    save(conn, event_id, decide(ok(), None, now), "gpt-6-luna", 1, now)
    rows = conn.execute(SAMPLE_SQL).fetchall()
    assert [(r[0], r[1], r[2], r[4], r[5]) for r in rows] == [(event_id, "Signal A", None, 1, GOOD["summary"])]
