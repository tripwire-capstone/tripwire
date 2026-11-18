"""Tests for the clusterer's prepare step (summarize + embed new events) and its wiring into the loop."""

import logging
import re
import threading
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from tests.clustering_data import add_event, add_failed_summary, add_signal, add_summary
from tripwire.clusterer import prepare as prepare_module
from tripwire.clusterer.embed import FakeEmbedder
from tripwire.clusterer.llm_client import FakeLLM, LLMResult
from tripwire.clusterer.loop import run_pass
from tripwire.clusterer.prepare import Preparer
from tripwire.clusterer.settings import load_settings
from tripwire.llm import LLMBadOutput, LLMServiceError, LLMUsage

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
    original = prepare_module.start_summary

    def recording(conn_, event_id, *args):
        seen.append(event_id)
        return original(conn_, event_id, *args)

    prepare_module.start_summary = recording
    try:
        preparer(FakeLLM([GOOD] * 3)).prepare_events(conn, 10)
    finally:
        prepare_module.start_summary = original
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


def test_a_crashed_event_does_not_count_as_stalled_for_the_health_check(conn, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai-key-1111")   # health rules apply only with a key
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


# ------------------------------------------------------------------ several summaries at once (SIG-111)

BARRIER_TIMEOUT = 5   # seconds; only reached when a chunk's calls are NOT running at the same time


class EventLLM:
    """A fake model that answers by event, so it's safe from several threads at once.

    FakeLLM plays back one script in order: with 4 threads, which call gets which answer would
    depend on timing. Here each event's prompt names the event (labelled_events), and `answers`
    maps an event ID to its answer (an output dict, or an exception to raise); others get GOOD.
    `barrier`: every call waits until the whole chunk has arrived, which proves the calls overlap.
    `on_call(event_id)`: runs during the call (e.g. to move a fake clock).
    """

    def __init__(self, answers=None, barrier=None, on_call=None):
        self.answers = answers or {}
        self.barrier = barrier
        self.on_call = on_call
        self.calls = []            # event IDs, in the order the calls started
        self.peak = 0              # most calls in flight at the same time
        self._running = 0
        self._lock = threading.Lock()

    def complete(self, model, messages, json_schema):
        event_id = int(re.search(r"tripwire-test-event-(\d+)", messages[1]["content"]).group(1))
        with self._lock:
            self.calls.append(event_id)
            self._running += 1
            self.peak = max(self.peak, self._running)
        try:
            if self.barrier:
                self.barrier.wait()
            if self.on_call:
                self.on_call(event_id)
            answer = self.answers.get(event_id, GOOD)
            if isinstance(answer, Exception):
                raise answer
            return LLMResult(output=answer, usage=LLMUsage(100, 0, 20, 0.00002))
        finally:
            with self._lock:
                self._running -= 1


def labelled_events(conn, version_id, count):
    """`count` unprepared events, oldest first, each named in its own prompt (for EventLLM)."""
    ids = [unembedded_event(conn, version_id, created_minutes_ago=count - index) for index in range(count)]
    for event_id in ids:
        conn.execute("UPDATE signal_events SET description = %s WHERE id = %s",
                     (f"tripwire-test-event-{event_id}", event_id))
    return ids


def preparer_with_threads(llm, threads=4, clock=None, embedder=None):
    return Preparer(llm, embedder or FakeEmbedder(), "gpt-6-luna", 1, threads=threads,
                    clock=clock or FakeClock(), now=lambda: NOW)


def summary_rows(conn):
    """event ID -> (status, error_kind) for every summary row."""
    return {row[0]: (row[1], row[2]) for row in conn.execute(
        "SELECT event_id, status, error_kind FROM event_summaries")}


def test_four_threads_run_a_chunk_of_four_calls_at_once_oldest_events_first(conn):
    _, version_id = add_signal(conn)
    ids = labelled_events(conn, version_id, 8)
    llm = EventLLM(barrier=threading.Barrier(4, timeout=BARRIER_TIMEOUT))

    counts = preparer_with_threads(llm).prepare_events(conn, 50)
    assert llm.peak == 4                                # all 4 waited at the barrier together
    assert set(llm.calls[:4]) == set(ids[:4])           # chunk 1 = the 4 oldest events
    assert counts == {"summarized": 8, "embedded": 8}


def test_a_service_error_mid_chunk_saves_the_whole_chunk_and_starts_no_more_chunks(conn):
    _, version_id = add_signal(conn)
    ids = labelled_events(conn, version_id, 10)
    rate_limited = ids[5]                               # 6th event: chunk 2 (events 5-8)
    llm = EventLLM({rate_limited: LLMServiceError("rate limited")})

    counts = preparer_with_threads(llm).prepare_events(conn, 50)
    assert sorted(llm.calls) == sorted(ids[:8])         # chunks 1 and 2 only
    rows = summary_rows(conn)
    assert rows[rate_limited] == ("pending", "service")
    assert all(rows[event_id] == ("done", None) for event_id in ids[:8] if event_id != rate_limited)
    assert ids[8] not in rows and ids[9] not in rows    # never tried: picked up next pass
    assert counts == {"summarized": 7, "retrying": 1, "left_for_next_pass": 2, "embedded": 7}


def test_two_service_errors_in_one_chunk_still_stop_once(conn):
    _, version_id = add_signal(conn)
    ids = labelled_events(conn, version_id, 10)
    llm = EventLLM({ids[0]: LLMServiceError("rate limited"), ids[2]: LLMServiceError("rate limited")})

    counts = preparer_with_threads(llm).prepare_events(conn, 50)
    assert sorted(llm.calls) == sorted(ids[:4])
    assert counts == {"summarized": 2, "retrying": 2, "left_for_next_pass": 6, "embedded": 2}


def test_the_time_budget_is_checked_before_each_chunk(conn):
    _, version_id = add_signal(conn)
    ids = labelled_events(conn, version_id, 12)
    clock = FakeClock()
    lock = threading.Lock()

    def each_chunk_takes_45_seconds(event_id):
        with lock:
            clock.seconds += 45 / 4

    llm = EventLLM(on_call=each_chunk_takes_45_seconds)
    counts = preparer_with_threads(llm, clock=clock).prepare_events(conn, 50)
    # Chunk 1 ends at 45 s; chunk 2 starts (45 < 60) and ends at 90 s; chunk 3 never starts.
    assert sorted(llm.calls) == sorted(ids[:8])
    assert (counts["summarized"], counts["left_for_next_pass"]) == (8, 4)


def test_a_bug_in_one_call_crashes_only_that_event(conn, caplog):
    _, version_id = add_signal(conn)
    ids = labelled_events(conn, version_id, 6)
    buggy = ids[1]
    llm = EventLLM({buggy: KeyError("reason")})

    counts = preparer_with_threads(llm).prepare_events(conn, 50)
    rows = summary_rows(conn)
    assert rows[buggy] == ("failed", "crashed")
    assert all(rows[event_id] == ("done", None) for event_id in ids if event_id != buggy)
    assert (counts["crashed"], counts["summarized"]) == (1, 5)   # chunk 2 still ran
    assert "hit a bug in our code" in caplog.text and "KeyError" in caplog.text


def test_a_bug_building_one_event_crashes_it_before_it_is_sent(conn, monkeypatch):
    _, version_id = add_signal(conn)
    ids = labelled_events(conn, version_id, 4)
    buggy = ids[2]
    original = prepare_module.start_summary

    def broken_for_one(conn_, event_id, prompt):
        if event_id == buggy:
            raise ValueError("bug in build_messages")
        return original(conn_, event_id, prompt)

    monkeypatch.setattr(prepare_module, "start_summary", broken_for_one)
    llm = EventLLM()
    counts = preparer_with_threads(llm).prepare_events(conn, 50)
    assert buggy not in llm.calls                       # never sent, never paid for
    assert sorted(llm.calls) == sorted(set(ids) - {buggy})
    assert summary_rows(conn)[buggy] == ("failed", "crashed")
    assert (counts["crashed"], counts["summarized"]) == (1, 3)


def test_a_database_outage_mid_chunk_ends_the_step_and_later_results_are_lost(conn):
    # Agreed trade-off: results are saved in chunk order, so one saved before the outage stays
    # saved; the ones after it were paid for but are lost (summarized again next pass).
    _, version_id = add_signal(conn)
    ids = labelled_events(conn, version_id, 4)
    llm = EventLLM({ids[1]: psycopg.OperationalError("server closed the connection")})

    with pytest.raises(psycopg.OperationalError):
        preparer_with_threads(llm).prepare_events(conn, 50)
    assert sorted(llm.calls) == sorted(ids)             # all 4 calls were made
    assert set(summary_rows(conn)) == {ids[0]}          # only the one before the outage was saved


def test_every_event_summarized_in_a_pass_is_embedded_in_that_pass_with_no_thread_left(conn):
    # The notifier's assumption: an event's embedding is saved, then assign places it, in the
    # same pass. So nothing may still be running when prepare_events returns.
    _, version_id = add_signal(conn)
    labelled_events(conn, version_id, 10)
    threads_before = threading.active_count()

    counts = preparer_with_threads(EventLLM()).prepare_events(conn, 50)
    assert threading.active_count() == threads_before
    done = {row[0] for row in conn.execute("SELECT event_id FROM event_summaries WHERE status = 'done'")}
    embedded = {row[0] for row in conn.execute("SELECT event_id FROM event_embeddings")}
    assert done == embedded and len(done) == 10
    assert counts == {"summarized": 10, "embedded": 10}


def test_one_timing_line_per_chunk(conn, caplog):
    _, version_id = add_signal(conn)
    labelled_events(conn, version_id, 10)
    with caplog.at_level(logging.INFO, logger="clusterer.prepare"):
        preparer_with_threads(EventLLM()).prepare_events(conn, 50)
    lines = [record.getMessage() for record in caplog.records if "summaries in" in record.getMessage()]
    assert [line.split(" summaries in")[0] for line in lines] == ["prepare: 4", "prepare: 4", "prepare: 2"]
    assert all("slowest call" in line and "average" in line for line in lines)


def test_preparer_refuses_fewer_than_one_thread():
    with pytest.raises(ValueError, match="threads must be at least 1, got 0"):
        preparer_with_threads(EventLLM(), threads=0)


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
