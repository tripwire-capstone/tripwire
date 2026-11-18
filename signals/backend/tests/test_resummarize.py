"""Tests for --as-version's re-summarizing step (tripwire/clusterer/resummarize.py), on real
Postgres + pgvector, with a fake summarizer and the fake embedder (no paid calls)."""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from tests.clustering_data import add_event, add_signal, add_summary
from tests.vectors import vectors_all_apart
from tripwire.clusterer.embed import FakeEmbedder
from tripwire.clusterer.llm_client import LLMResult
from tripwire.clusterer.resummarize import SCHEMA_PREFIX, build_version_schema, drop_schema
from tripwire.clusterer.summarize import load_prompt
from tripwire.clusterer.sweep import replay
from tripwire.llm import LLMBadOutput, LLMUsage

NOW = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
USAGE = LLMUsage(100, 0, 20, 0.00002)


class KeyedLLM:
    """Answers by which event the request is about (safe from several threads, unlike FakeLLM's
    script). `answers[description]` = a list played in order: an output dict or an exception."""

    def __init__(self, answers):
        self.answers = {key: list(value) for key, value in answers.items()}
        self.calls = []
        self._lock = threading.Lock()

    def complete(self, model, messages, json_schema):
        user = messages[1]["content"]
        key = next(key for key in self.answers if f"description: {key}" in user)
        with self._lock:
            self.calls.append(key)
            item = self.answers[key].pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(output=item, usage=USAGE)


def answer(summary):
    return {"cause": "test cause", "summary": summary, "details": []}


def event_with(conn, version_id, vector, description, summary, embedded_at):
    """An event as the live pipeline left it: v1 summary + embedding at a known time."""
    event_id = add_event(conn, version_id, vector, session_id=f"conversation-{description}")
    conn.execute("UPDATE signal_events SET description = %s WHERE id = %s", (description, event_id))
    conn.execute("UPDATE event_embeddings SET created_at = %s WHERE event_id = %s", (embedded_at, event_id))
    add_summary(conn, event_id, summary)
    return event_id


@pytest.fixture
def two_events(conn):
    _, version_id = add_signal(conn)
    first_vector, second_vector = vectors_all_apart(2, 0.9)
    first = event_with(conn, version_id, first_vector, "first", "old first", NOW - timedelta(minutes=5))
    second = event_with(conn, version_id, second_vector, "second", "old second", NOW - timedelta(minutes=1))
    return first, second


def version_schemas(conn):
    """How many asver_ schemas exist (counted before and after, so a leftover elsewhere can't fail a test)."""
    return conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname LIKE %s",
                        (SCHEMA_PREFIX.replace("_", r"\_") + "%",)).fetchone()[0]


def in_schema(conn, schema, query, params=()):
    return conn.execute(query.replace("{s}", schema), params).fetchall()


def test_new_schema_has_new_summaries_and_the_original_embedding_times(conn, two_events):
    first, second = two_events
    path_before = conn.execute("SHOW search_path").fetchone()[0]
    llm = KeyedLLM({"first": [answer("new first")], "second": [answer("new second")]})

    schema, updates = build_version_schema(conn, [first, second], llm, FakeEmbedder(), "test-model",
                                           load_prompt(5), workers=2, now=lambda: NOW)
    try:
        assert schema.startswith(SCHEMA_PREFIX)
        assert {event_id: update.status for event_id, update in updates.items()} == {first: "done", second: "done"}
        assert in_schema(conn, schema, "SELECT event_id, summary, prompt_version FROM {s}.event_summaries "
                                       "ORDER BY event_id") == [(first, "new first", 5), (second, "new second", 5)]
        # Same timestamps as the originals, so the sweep replays the same passes.
        assert in_schema(conn, schema, "SELECT event_id, created_at FROM {s}.event_embeddings ORDER BY event_id") == \
            [(first, NOW - timedelta(minutes=5)), (second, NOW - timedelta(minutes=1))]
        # The embeddings are of the NEW summaries, not copies of the old vectors.
        assert in_schema(conn, schema, "SELECT model FROM {s}.event_embeddings") == [("fake-embedding",)] * 2
        # The real tables are untouched, and the connection is back where it was.
        assert conn.execute("SELECT summary FROM event_summaries ORDER BY event_id").fetchall() == \
            [("old first",), ("old second",)]
        assert conn.execute("SHOW search_path").fetchone()[0] == path_before
    finally:
        drop_schema(conn, schema)


def test_a_failed_call_gets_one_retry(conn, two_events):
    first, second = two_events
    llm = KeyedLLM({"first": [LLMBadOutput("blank summary", usage=USAGE), answer("new first")],
                    "second": [answer("new second")]})

    schema, updates = build_version_schema(conn, [first, second], llm, FakeEmbedder(), "test-model",
                                           load_prompt(5), workers=2, now=lambda: NOW)
    try:
        assert sorted(llm.calls) == ["first", "first", "second"]
        assert updates[first].status == "done" and updates[first].attempts == 2
    finally:
        drop_schema(conn, schema)


def test_an_event_still_failing_after_the_retry_is_left_without_an_embedding(conn, two_events):
    first, second = two_events
    llm = KeyedLLM({"first": [LLMBadOutput("blank summary", usage=USAGE)] * 2, "second": [answer("new second")]})

    schema, updates = build_version_schema(conn, [first, second], llm, FakeEmbedder(), "test-model",
                                           load_prompt(5), workers=2, now=lambda: NOW)
    try:
        assert updates[first].status != "done"
        assert in_schema(conn, schema, "SELECT event_id FROM {s}.event_embeddings") == [(second,)]
    finally:
        drop_schema(conn, schema)


def test_a_bug_drops_the_new_schema_and_raises(conn, two_events):
    first, second = two_events
    path_before, schemas_before = conn.execute("SHOW search_path").fetchone()[0], version_schemas(conn)
    llm = KeyedLLM({"first": [RuntimeError("bug in our code")], "second": [answer("new second")]})

    with pytest.raises(RuntimeError, match="bug in our code"):
        build_version_schema(conn, [first, second], llm, FakeEmbedder(), "test-model", load_prompt(5),
                             workers=2, now=lambda: NOW)
    assert version_schemas(conn) == schemas_before
    assert conn.execute("SHOW search_path").fetchone()[0] == path_before


def test_the_sweep_replays_on_the_new_schema(conn, two_events):
    first, second = two_events
    llm = KeyedLLM({"first": [answer("new first")], "second": [answer("new second")]})
    schema, _ = build_version_schema(conn, [first, second], llm, FakeEmbedder(), "test-model",
                                     load_prompt(5), workers=2, now=lambda: NOW)
    try:
        clusters = replay(conn, 0.30, source_schema=schema, environ={})
        assert set(clusters) == {first, second}     # both embedded events replayed (too few to cluster)
    finally:
        drop_schema(conn, schema)


def test_report_as_version_end_to_end(conn, two_events, tmp_path, monkeypatch, capsys):
    """The CLI path: answer key -> re-summarize -> sweep -> report + CSV, with fakes for the paid calls."""
    import argparse
    import csv

    from tripwire.clusterer import embed, llm_client, report

    first, second = two_events
    traces = dict(conn.execute("SELECT e.id, t.laminar_trace_id::text FROM signal_events e "
                               "JOIN traces t ON t.id = e.trace_id"))
    bugs_by_trace = {traces[first]: frozenset({"timeout"}), traces[second]: frozenset({"timeout"})}
    llm = KeyedLLM({"first": [answer("tool times out")], "second": [answer("tool times out again")]})
    monkeypatch.setattr(llm_client, "OpenAIClient", lambda api_key: llm)
    monkeypatch.setattr(embed, "OpenAIEmbedder", lambda api_key: embed.FakeEmbedder())
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    out = tmp_path / "summaries.csv"
    schemas_before = version_schemas(conn)
    args = argparse.Namespace(as_version=5, model="test-model", workers=2, thresholds=[0.20, 0.30],
                              summaries_out=str(out))

    report.run_as_version(conn, args, bugs_by_trace)

    printed = capsys.readouterr().out
    assert "summarizer prompt v5: 2 events" in printed
    assert "Sweep (replay of the real clusterer at each threshold)" in printed
    assert " now " not in printed                          # nothing is clustered in the new schema
    rows = list(csv.DictReader(out.open()))
    assert [(row["summary_before"], row["summary_after"], row["label"]) for row in rows] == \
        [("old first", "tool times out", "timeout"), ("old second", "tool times out again", "timeout")]
    assert version_schemas(conn) <= schemas_before         # it also clears leftovers from crashed runs
