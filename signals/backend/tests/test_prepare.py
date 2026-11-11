"""Tests for the clusterer's prepare step (summarize + embed new events) and its wiring into the loop."""

from datetime import datetime, timedelta, timezone

import pytest

from tests.clustering_data import add_event, add_failed_summary, add_signal, add_summary
from tripwire.clusterer import prepare as prepare_module
from tripwire.clusterer.embed import FakeEmbedder
from tripwire.clusterer.llm_client import FakeLLM, LLMBadOutput, LLMServiceError
from tripwire.clusterer.loop import run_pass
from tripwire.clusterer.prepare import Preparer
from tripwire.clusterer.settings import load_settings

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
GOOD = {"cause": "assistant reply", "summary": "The assistant gives a wrong answer, in booking."}


class FakeClock:
    """Seconds since start; tests move it forward to use up the time budget."""
    def __init__(self):
        self.seconds = 0.0

    def __call__(self):
        return self.seconds


def preparer(llm, embedder=None, clock=None, now=NOW):
    return Preparer(llm, embedder or FakeEmbedder(), "gpt-6-luna", 1,
                    clock=clock or FakeClock(), now=lambda: now)


def unembedded_event(conn, version_id, **kwargs):
    return add_event(conn, version_id, None, **kwargs)


def summarized_ids(conn):
    return [row[0] for row in conn.execute(
        "SELECT event_id FROM event_summaries WHERE status = 'done' ORDER BY updated_at, event_id")]


def summarized_order(llm):
    """Which events the fake model was asked about, in order (each prompt carries the event's payload)."""
    return [request["messages"][1]["content"] for request in llm.requests]


# ------------------------------------------------------------------ which events, in what order

def test_live_events_go_before_backfill_then_oldest_first(conn):
    _, version_id = add_signal(conn)
    backfill_old = unembedded_event(conn, version_id, source="backfill", created_minutes_ago=90)
    live_new = unembedded_event(conn, version_id, created_minutes_ago=1)
    live_old = unembedded_event(conn, version_id, created_minutes_ago=30)

    seen = []
    original = prepare_module.summarize_event

    def recording(conn_, llm, event_id, *args):
        seen.append(event_id)
        return original(conn_, llm, event_id, *args)

    prepare_module.summarize_event = recording
    try:
        preparer(FakeLLM([GOOD] * 3)).prepare_events(conn, 10)
    finally:
        prepare_module.summarize_event = original
    assert seen == [live_old, live_new, backfill_old]


def test_picks_never_tried_and_waits_that_are_over_but_not_finished_or_still_waiting(conn):
    _, version_id = add_signal(conn)
    never_tried = unembedded_event(conn, version_id)
    wait_over = unembedded_event(conn, version_id)
    still_waiting = unembedded_event(conn, version_id)
    done = unembedded_event(conn, version_id)
    failed = unembedded_event(conn, version_id)
    for event_id, not_before in ((wait_over, NOW - timedelta(seconds=1)), (still_waiting, NOW + timedelta(minutes=1))):
        conn.execute("""INSERT INTO event_summaries (event_id, status, model, prompt_version, attempts,
                                                     not_before, error_kind, last_error)
                        VALUES (%s, 'pending', 'm', 1, 1, %s, 'bad_output', 'x')""", (event_id, not_before))
    add_summary(conn, done, "Already summarized.")
    add_failed_summary(conn, failed)

    llm = FakeLLM([GOOD] * 5)
    counts = preparer(llm).prepare_events(conn, 10)
    assert len(llm.requests) == 2
    assert counts["summarized"] == 2
    assert set(summarized_ids(conn)) == {never_tried, wait_over, done}


def test_limit_caps_how_many_events_are_summarized(conn):
    _, version_id = add_signal(conn)
    for _ in range(4):
        unembedded_event(conn, version_id)
    llm = FakeLLM([GOOD] * 4)
    assert preparer(llm).prepare_events(conn, 2)["summarized"] == 2
    assert len(llm.requests) == 2


# ------------------------------------------------------------------ stopping early

def test_time_budget_stops_starting_new_summaries(conn):
    _, version_id = add_signal(conn)
    for _ in range(3):
        unembedded_event(conn, version_id)
    clock = FakeClock()

    class SlowLLM(FakeLLM):
        def complete(self, *args):
            clock.seconds += 45          # each call takes 45 s: the 2nd ends past the 60 s budget
            return super().complete(*args)

    llm = SlowLLM([GOOD] * 3)
    counts = preparer(llm, clock=clock).prepare_events(conn, 10)
    assert len(llm.requests) == 2
    assert (counts["summarized"], counts["left_for_next_pass"]) == (2, 1)


def test_a_service_error_stops_summarizing_but_embedding_still_runs(conn):
    _, version_id = add_signal(conn)
    already_summarized = unembedded_event(conn, version_id)
    add_summary(conn, already_summarized, "Waiting for its vector.")
    for _ in range(3):
        unembedded_event(conn, version_id)

    llm = FakeLLM([LLMServiceError("rate limited"), GOOD, GOOD])
    embedder = FakeEmbedder()
    counts = preparer(llm, embedder).prepare_events(conn, 10)
    assert len(llm.requests) == 1                       # stopped after the first error
    assert (counts["retrying"], counts["left_for_next_pass"], counts["embedded"]) == (1, 2, 1)
    assert embedder.calls == [["Waiting for its vector."]]


def test_a_bad_output_does_not_stop_the_pass(conn):
    _, version_id = add_signal(conn)
    for _ in range(2):
        unembedded_event(conn, version_id)
    counts = preparer(FakeLLM([LLMBadOutput("broken"), GOOD])).prepare_events(conn, 10)
    assert (counts["retrying"], counts["summarized"]) == (1, 1)


# ------------------------------------------------------------------ bugs in our own code

class BuggyOnce(FakeLLM):
    """Raises a bug-like error on the first call, then answers normally."""
    def __init__(self, script, error=None):
        super().__init__(script)
        self.error = error or KeyError("reason")
        self.calls = 0

    def complete(self, *args):
        self.calls += 1
        if self.calls == 1:
            raise self.error
        return super().complete(*args)


def test_a_bug_on_one_event_is_saved_as_crashed_and_the_others_still_run(conn, caplog):
    _, version_id = add_signal(conn)
    buggy = unembedded_event(conn, version_id, created_minutes_ago=10)
    fine = unembedded_event(conn, version_id, created_minutes_ago=5)

    worker = preparer(BuggyOnce([GOOD]))
    counts = worker.prepare_events(conn, 10)
    assert (counts["crashed"], counts["summarized"]) == (1, 1)
    assert "hit a bug in our code" in caplog.text and "KeyError" in caplog.text
    row = conn.execute("SELECT status, error_kind, last_error, attempts FROM event_summaries WHERE event_id = %s",
                       (buggy,)).fetchone()
    assert row == ("failed", "crashed", "KeyError: 'reason'", 1)
    assert summarized_ids(conn) == [fine]

    # Next pass: the crashed event is finished, so it isn't retried (or paid for) again.
    calls_before = worker.llm.calls
    assert worker.prepare_events(conn, 10) == {}
    assert worker.llm.calls == calls_before


def test_a_crashed_event_does_not_count_as_stalled_for_the_health_check(conn):
    from tests.test_health import settings_for_tests
    from tripwire.clusterer.health import check_clustering
    _, version_id = add_signal(conn)
    unembedded_event(conn, version_id, created_minutes_ago=30)     # older than the 15 min limit
    assert not check_clustering(conn, settings_for_tests()).healthy    # stalled before the crash is saved
    preparer(BuggyOnce([])).prepare_events(conn, 10)
    assert check_clustering(conn, settings_for_tests()).healthy        # finished (failed) afterwards


def test_a_database_outage_is_not_a_crash(conn):
    import psycopg
    _, version_id = add_signal(conn)
    event_id = unembedded_event(conn, version_id)
    with pytest.raises(psycopg.OperationalError):
        preparer(BuggyOnce([], error=psycopg.OperationalError("server closed the connection"))).prepare_events(conn, 10)
    assert conn.execute("SELECT count(*) FROM event_summaries").fetchone()[0] == 0   # retried next pass


def test_if_even_saving_the_crash_fails_the_event_is_skipped_until_restart(conn, monkeypatch, caplog):
    _, version_id = add_signal(conn)
    unembedded_event(conn, version_id)

    def broken_save(*args):
        raise ValueError("a bug in save")

    monkeypatch.setattr(prepare_module, "save", broken_save)
    worker = preparer(BuggyOnce([]))
    worker.prepare_events(conn, 10)
    assert "couldn't save the crash" in caplog.text
    assert len(worker.skipped) == 1
    calls_before = worker.llm.calls
    worker.prepare_events(conn, 10)
    assert worker.llm.calls == calls_before            # skipped, not retried every pass


# ------------------------------------------------------------------ embedding

def test_embeds_every_done_summary_in_one_call_and_saves_each_vector_to_its_event(conn):
    _, version_id = add_signal(conn)
    first, second = unembedded_event(conn, version_id), unembedded_event(conn, version_id)
    add_summary(conn, first, "First summary.")
    add_summary(conn, second, "Second summary.")
    embedder = FakeEmbedder()

    counts = preparer(FakeLLM([]), embedder).prepare_events(conn, 10)
    assert counts == {"embedded": 2}
    assert embedder.calls == [["First summary.", "Second summary."]]
    saved = dict(conn.execute("SELECT event_id, model FROM event_embeddings").fetchall())
    assert saved == {first: "fake-embedding", second: "fake-embedding"}


def test_an_embedding_service_error_leaves_summaries_for_the_next_pass(conn):
    _, version_id = add_signal(conn)
    event_id = unembedded_event(conn, version_id)
    add_summary(conn, event_id, "A summary.")
    counts = preparer(FakeLLM([]), FakeEmbedder(fail_with=LLMServiceError("down"))).prepare_events(conn, 10)
    assert counts == {}
    assert conn.execute("SELECT count(*) FROM event_embeddings").fetchone()[0] == 0
    # Next pass, the embedder is back: the same summary gets its vector.
    assert preparer(FakeLLM([])).prepare_events(conn, 10) == {"embedded": 1}


def test_an_event_summarized_this_pass_is_also_embedded_this_pass(conn):
    _, version_id = add_signal(conn)
    unembedded_event(conn, version_id)
    assert preparer(FakeLLM([GOOD])).prepare_events(conn, 10) == {"summarized": 1, "embedded": 1}


def test_nothing_to_do_returns_empty_counts(conn):
    assert preparer(FakeLLM([])).prepare_events(conn, 10) == {}


# ------------------------------------------------------------------ in the loop

def test_run_pass_with_the_real_prepare_summarizes_embeds_and_groups_new_events(conn):
    # Three conversations hitting the same problem: the fake model gives the same summary, the fake
    # embedder the same vector, so pool grouping forms one cluster named after that summary.
    signal_id, version_id = add_signal(conn)
    for conversation in ("c1", "c2", "c3"):
        unembedded_event(conn, version_id, session_id=conversation)
    settings = load_settings({"DATABASE_URL": "postgresql://unused", "OPENAI_API_KEY": "test-key"})

    worker = preparer(FakeLLM([GOOD] * 3))
    result = run_pass(conn, settings, prepare=worker.prepare_events)

    assert result.prepared == {"summarized": 3, "embedded": 3}
    assert (result.created, result.named, result.failed_steps) == (1, 1, [])
    name = conn.execute("SELECT name FROM clusters WHERE signal_id = %s", (signal_id,)).fetchone()[0]
    assert name == GOOD["summary"]
