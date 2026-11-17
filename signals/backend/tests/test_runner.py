"""The runner's job flow against a real Postgres, with fake judges."""

import uuid

import pytest

from tripwire.judge.types import Finding, JudgeError, JudgeResult, Usage
from tripwire.runner import jobs
from tripwire.runner.settings import Settings
import logging

from tripwire.project_keys import KeyWatch, save_key
from tripwire.runner.worker import MISSING_KEY_RETRY_SECONDS, ProjectClients, backoff_seconds, work_once

SETTINGS = Settings()


class UnusedClients:
    """Fake judges never use the Laminar client; they only see the OpenAI key."""

    def for_run(self, conn, project):
        return None, "sk-test"


CLIENTS = UnusedClients()


def add_run(conn, *, active=True, source="live"):
    """A signal, its version, a trace and one pending run. Returns (run_id, trace_id, version_id)."""
    project = conn.execute("SELECT id FROM projects").fetchone()[0]
    signal_id = conn.execute("INSERT INTO signals (project_id, name, active) VALUES (%s, %s, %s) RETURNING id",
                             (project, f"S-{uuid.uuid4()}", active)).fetchone()[0]
    version_id = conn.execute(
        """INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
           VALUES (%s, 1, 'x', '[]', 'm') RETURNING id""", (signal_id,)).fetchone()[0]
    trace_id = conn.execute(
        """INSERT INTO traces (project_id, laminar_trace_id, started_at, ended_at, status)
           VALUES (%s, %s, now(), now(), 'success') RETURNING id""", (project, str(uuid.uuid4()))).fetchone()[0]
    run_id = conn.execute("INSERT INTO signal_runs (trace_id, signal_version_id, source) VALUES (%s, %s, %s) RETURNING id",
                          (trace_id, version_id, source)).fetchone()[0]
    return run_id, trace_id, version_id


def result(findings=1, cost=0.01):
    return JudgeResult(findings=[Finding("warning", ["span-1"], {"n": i}, [f"S{i + 1}"])
                                 for i in range(findings)],
                       analysis="reasoning", raw_output="{}", judge_input="rendered trace",
                       dropped_findings=0, model="m", usage=Usage(100, 10, 20, cost))


def judge_that(outcome):
    def fake(laminar, trace, version, *, openai_api_key):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return fake


def run_row(conn, run_id):
    return conn.execute("""SELECT status, attempts, not_before > now(), last_error, input_tokens,
                                  cost_usd, rendered_trace, analysis
                           FROM signal_runs WHERE id = %s""", (run_id,)).fetchone()


def events(conn):
    return conn.execute("SELECT finding_index, payload, judge_input FROM signal_events ORDER BY finding_index").fetchall()


def ready(conn):
    """Skip the backoff so the next work_once can claim again."""
    conn.execute("UPDATE signal_runs SET not_before = NULL")


SERVICE = JudgeError("429", retryable=True, uses_attempt=False, usage=Usage(50, 0, 0, 0.005))
BAD = JudgeError("cut off twice", retryable=True, uses_attempt=True, usage=Usage(100, 0, 0, 0.01))


# ------------------------------------------------------------------ success


def test_success_writes_done_and_one_event_per_finding(conn):
    run_id, _, _ = add_run(conn)
    assert work_once(conn, SETTINGS, CLIENTS, judge_that(result(findings=3)))
    status, attempts, _, _, tokens, cost, _, analysis = run_row(conn, run_id)
    assert (status, attempts, tokens, float(cost), analysis) == ("done", 1, 100, 0.01, "reasoning")
    assert events(conn) == [(0, {"n": 0}, "rendered trace"), (1, {"n": 1}, "rendered trace"),
                            (2, {"n": 2}, "rendered trace")]


def test_each_event_keeps_its_own_evidence_labels(conn):
    add_run(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(result(findings=2)))
    assert conn.execute("SELECT evidence_labels FROM signal_events ORDER BY finding_index").fetchall() == \
        [(["S1"],), (["S2"],)]


def test_no_findings_is_done_with_no_events(conn):
    run_id, _, _ = add_run(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(result(findings=0)))
    assert run_row(conn, run_id)[0] == "done" and events(conn) == []


def test_nothing_to_claim_returns_false(conn):
    assert not work_once(conn, SETTINGS, CLIENTS, judge_that(result()))


# ------------------------------------------------------------------ errors


def test_service_error_gives_the_attempt_back_and_waits(conn):
    run_id, _, _ = add_run(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(SERVICE))
    status, attempts, waiting, error, tokens, _, _, _ = run_row(conn, run_id)
    assert (status, attempts, waiting, error, tokens) == ("pending", 0, True, "429", 50)


def test_waiting_run_is_not_claimed(conn):
    add_run(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(SERVICE))
    assert not work_once(conn, SETTINGS, CLIENTS, judge_that(result()))


def test_bad_output_uses_attempts_then_fails(conn):
    run_id, _, _ = add_run(conn)
    for expected in [("pending", 1), ("pending", 2), ("failed", 3)]:
        work_once(conn, SETTINGS, CLIENTS, judge_that(BAD))
        assert run_row(conn, run_id)[:2] == expected
        ready(conn)


def test_refusal_fails_at_once_and_keeps_the_rendered_trace(conn):
    run_id, _, _ = add_run(conn)
    refused = JudgeError("refused", retryable=False, uses_attempt=False, rendered_trace="the trace")
    work_once(conn, SETTINGS, CLIENTS, judge_that(refused))
    status, attempts, _, error, _, _, rendered, _ = run_row(conn, run_id)
    assert (status, attempts, error, rendered) == ("failed", 1, "refused", "the trace")


def test_unexpected_exception_counts_like_bad_output(conn):
    run_id, _, _ = add_run(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(RuntimeError("bug")))
    status, attempts, _, error, _, _, _, _ = run_row(conn, run_id)
    assert (status, attempts) == ("pending", 1) and "bug" in error


def test_cost_adds_up_across_attempts(conn):
    run_id, _, _ = add_run(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(SERVICE))
    ready(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(result(cost=0.02)))
    _, _, _, _, tokens, cost, _, _ = run_row(conn, run_id)
    assert (tokens, float(cost)) == (150, 0.025)


def test_unknown_cost_makes_the_total_unknown(conn):
    run_id, _, _ = add_run(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(SERVICE))
    ready(conn)
    work_once(conn, SETTINGS, CLIENTS, judge_that(result(cost=None)))
    assert run_row(conn, run_id)[5] is None


# ------------------------------------------------------------------ claiming


def test_inactive_signal_is_not_claimed(conn):
    add_run(conn, active=False)
    assert jobs.claim(conn, SETTINGS) is None


def test_live_runs_go_before_backfill(conn):
    add_run(conn, source="backfill")
    live_id, _, _ = add_run(conn, source="live")
    assert jobs.claim(conn, SETTINGS).run_id == live_id


def test_late_runner_cannot_overwrite_the_new_owner(conn):
    run_id, trace_id, version_id = add_run(conn)
    first = jobs.claim(conn, SETTINGS)
    conn.execute("UPDATE signal_runs SET lease_until = now() - interval '1 second' WHERE id = %s", (run_id,))
    second = jobs.claim(conn, SETTINGS)
    assert second.attempts == 2
    assert not jobs.record_success(conn, first, trace_id, version_id, result(findings=2))
    assert events(conn) == []
    assert jobs.record_success(conn, second, trace_id, version_id, result(findings=1))
    assert len(events(conn)) == 1


# ------------------------------------------------------------------ sweep


def test_sweep_fails_an_expired_lease_on_the_last_attempt(conn):
    run_id, _, _ = add_run(conn)
    conn.execute("""UPDATE signal_runs SET status = 'running', attempts = 3,
                    lease_until = now() - interval '1 second' WHERE id = %s""", (run_id,))
    assert jobs.sweep(conn, SETTINGS) == (1, 0)
    assert run_row(conn, run_id)[0] == "failed"


def test_sweep_gives_up_on_old_runs_of_active_signals_only(conn):
    old_id, _, _ = add_run(conn)
    off_id, _, _ = add_run(conn, active=False)
    conn.execute("UPDATE signal_runs SET created_at = now() - interval '25 hours'")
    assert jobs.sweep(conn, SETTINGS) == (0, 1)
    assert run_row(conn, old_id)[0] == "failed"
    assert run_row(conn, off_id)[0] == "pending"


# ------------------------------------------------------------------ settings and backoff


def test_lease_too_short_for_the_judge_is_rejected():
    with pytest.raises(ValueError):
        Settings(lease_seconds=100).check()
    Settings().check()


def test_backoff_uses_retry_after_and_stays_under_the_cap():
    assert backoff_seconds(7, 0, SETTINGS) == 7
    assert backoff_seconds(None, 10_000, SETTINGS, rand=lambda: 1.0) == SETTINGS.max_backoff_seconds
    assert 3.75 <= backoff_seconds(None, 0, SETTINGS, rand=lambda: 0.0) <= 6.25


# ------------------------------------------------------------------ keys (looked up per run)

SECRET = "ab" * 32


@pytest.fixture
def keys_env(monkeypatch):
    monkeypatch.setenv("TRIPWIRE_SECRET_KEY", SECRET)
    monkeypatch.setenv("LMNR_PROJECT_API_KEY", "env-laminar-key-0000")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai-key-1111")


def project_clients():
    return ProjectClients(KeyWatch(logging.getLogger("test")))


def judge_recording(seen):
    def fake(laminar, trace, version, *, openai_api_key):
        seen.append((laminar._client.headers["Authorization"], openai_api_key))
        return result(findings=0)
    return fake


def test_runner_judges_with_the_environment_keys_when_none_are_saved(conn, keys_env):
    add_run(conn)
    seen = []
    work_once(conn, SETTINGS, project_clients(), judge_recording(seen))
    assert seen == [("Bearer env-laminar-key-0000", "sk-env-openai-key-1111")]


def test_runner_uses_a_key_saved_in_settings_on_the_next_run(conn, keys_env):
    clients = project_clients()
    seen = []
    add_run(conn)
    work_once(conn, SETTINGS, clients, judge_recording(seen))
    project = conn.execute("SELECT id FROM projects").fetchone()[0]
    save_key(conn, project, "openai", "sk-saved-key-2222222")
    add_run(conn)
    work_once(conn, SETTINGS, clients, judge_recording(seen))
    assert [openai for _, openai in seen] == ["sk-env-openai-key-1111", "sk-saved-key-2222222"]


@pytest.mark.parametrize("unset, message", [
    (["OPENAI_API_KEY"], "no OpenAI key: add it in Settings"),
    (["LMNR_PROJECT_API_KEY"], "no Laminar key: add it in Settings"),
    (["LMNR_PROJECT_API_KEY", "OPENAI_API_KEY"], "no Laminar or OpenAI key: add it in Settings"),
])
def test_runner_with_a_key_missing_waits_without_using_an_attempt(conn, keys_env, monkeypatch, unset, message):
    for name in unset:
        monkeypatch.delenv(name)
    run_id, _, _ = add_run(conn)
    judged = []
    work_once(conn, SETTINGS, project_clients(), judge_recording(judged))
    status, attempts, waiting, last_error = run_row(conn, run_id)[:4]
    assert (status, attempts, waiting, last_error) == ("pending", 0, True, message)
    assert judged == []
    wait = conn.execute("SELECT extract(epoch FROM not_before - now()) FROM signal_runs WHERE id = %s",
                        (run_id,)).fetchone()[0]
    assert MISSING_KEY_RETRY_SECONDS - 5 < wait <= MISSING_KEY_RETRY_SECONDS


def test_runner_runs_a_waiting_run_once_the_key_is_saved(conn, keys_env, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    run_id, _, _ = add_run(conn)
    clients = project_clients()
    work_once(conn, SETTINGS, clients, judge_recording([]))
    save_key(conn, conn.execute("SELECT id FROM projects").fetchone()[0], "openai", "sk-saved-key-2222222")
    ready(conn)
    work_once(conn, SETTINGS, clients, judge_recording([]))
    assert run_row(conn, run_id)[:2] == ("done", 1)
