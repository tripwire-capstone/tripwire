"""Tests for the clusterer's health checks (Q12's rules, --alive, the warning reporter, the command)."""

import logging

import pytest

from tests.clustering_data import add_event, add_failed_summary, add_signal, add_summary
from tests.vectors import base_vector
from tripwire.clusterer import __main__ as clusterer_main
from tripwire.clusterer.health import (
    UNHEALTHY_PREFIX,
    HealthReport,
    UnhealthyReporter,
    check_alive,
    check_clustering,
    main,
    record_pass,
)
from tripwire.clusterer.settings import load_settings


def settings_for_tests(**overrides):
    environ = {"DATABASE_URL": "postgresql://unused"}
    environ.update({name: str(value) for name, value in overrides.items()})
    return load_settings(environ)


SETTINGS = settings_for_tests()   # stall 15 min, failure run 10


# ------------------------------------------------------------------ rule 1: a live event stalled

def test_check_clustering_is_healthy_with_no_events(conn):
    assert check_clustering(conn, SETTINGS).healthy


def test_check_clustering_flags_live_event_unfinished_past_the_limit(conn):
    _, version_id = add_signal(conn)
    event_id = add_event(conn, version_id, None, created_minutes_ago=23)

    report = check_clustering(conn, SETTINGS)
    assert not report.healthy
    assert report.problems == [f"event {event_id} has waited 23 minutes to be clustered (limit 15)"]


def test_check_clustering_ignores_unfinished_event_within_the_limit(conn):
    _, version_id = add_signal(conn)
    add_event(conn, version_id, None, created_minutes_ago=10)
    assert check_clustering(conn, SETTINGS).healthy


def test_check_clustering_counts_embedded_events_as_finished(conn):
    # Embedded = clustered or waiting in the pool: both are "finished" for the health check.
    _, version_id = add_signal(conn)
    add_event(conn, version_id, base_vector(), created_minutes_ago=60)
    assert check_clustering(conn, SETTINGS).healthy


def test_check_clustering_counts_failed_summaries_as_finished(conn):
    _, version_id = add_signal(conn)
    event_id = add_event(conn, version_id, None, created_minutes_ago=60)
    add_failed_summary(conn, event_id)
    assert check_clustering(conn, SETTINGS).healthy


def test_check_clustering_ignores_backfill_events_waiting_their_turn(conn):
    # Decided Nov 11: backfill is prepared after live events by design (#14), so it may wait.
    _, version_id = add_signal(conn)
    add_event(conn, version_id, None, created_minutes_ago=90, source="backfill")
    assert check_clustering(conn, SETTINGS).healthy


def test_check_clustering_names_the_oldest_stalled_event(conn):
    _, version_id = add_signal(conn)
    add_event(conn, version_id, None, created_minutes_ago=20)
    oldest = add_event(conn, version_id, None, created_minutes_ago=40)
    assert f"event {oldest} has waited 40 minutes" in check_clustering(conn, SETTINGS).problems[0]


# ------------------------------------------------------------------ rule 2: summaries all failing

def failed_events(conn, version_id, count):
    for index in range(count):
        add_failed_summary(conn, add_event(conn, version_id, None))


def test_check_clustering_flags_the_last_ten_summaries_all_failing(conn):
    _, version_id = add_signal(conn)
    failed_events(conn, version_id, 10)
    assert check_clustering(conn, SETTINGS).problems == ["the last 10 summaries all failed"]


def test_check_clustering_needs_a_full_run_of_failures(conn):
    _, version_id = add_signal(conn)
    failed_events(conn, version_id, 9)                     # fewer than 10: not enough to judge
    assert check_clustering(conn, SETTINGS).healthy


def test_check_clustering_is_healthy_when_a_recent_summary_succeeded(conn):
    _, version_id = add_signal(conn)
    failed_events(conn, version_id, 12)
    add_summary(conn, add_event(conn, version_id, None), "A summary that worked")   # newest
    assert check_clustering(conn, SETTINGS).healthy


def test_check_clustering_ignores_old_failures_followed_by_successes(conn):
    _, version_id = add_signal(conn)
    failed_events(conn, version_id, 10)
    for index in range(10):
        add_summary(conn, add_event(conn, version_id, None), f"Worked {index}")
    assert check_clustering(conn, SETTINGS).healthy


def test_check_clustering_reports_both_rules_together(conn):
    _, version_id = add_signal(conn)
    add_event(conn, version_id, None, created_minutes_ago=30)
    failed_events(conn, version_id, 10)
    assert len(check_clustering(conn, SETTINGS).problems) == 2


def test_health_report_summary_starts_with_the_alarm_prefix():
    report = HealthReport(problems=["the last 10 summaries all failed"])
    assert report.summary() == f"{UNHEALTHY_PREFIX} the last 10 summaries all failed"
    assert HealthReport().summary() == "healthy"


# ------------------------------------------------------------------ --alive

def test_check_alive_passes_right_after_a_pass(tmp_path):
    path = tmp_path / "last-pass"
    record_pass(path, now=lambda: 1000.0)
    alive, message = check_alive(path, max_age_minutes=3, now=lambda: 1030.0)
    assert alive and message == "alive: last pass 30 s ago"


def test_check_alive_fails_when_no_pass_for_too_long(tmp_path):
    path = tmp_path / "last-pass"
    record_pass(path, now=lambda: 1000.0)
    alive, message = check_alive(path, max_age_minutes=3, now=lambda: 1000.0 + 5 * 60)
    assert not alive and message == "no clusterer pass for 5.0 minutes (limit 3)"


def test_check_alive_fails_before_the_first_pass(tmp_path):
    assert check_alive(tmp_path / "missing", max_age_minutes=3) == (False, "no clusterer pass has finished yet")


def test_tick_records_the_pass_even_when_the_pass_fails(tmp_path):
    # A pass that can't reach the database still counts as "alive": only a frozen loop should fail
    # --alive, because that's the only case a restart fixes.
    settings = settings_for_tests(CLUSTERER_LAST_PASS_FILE=tmp_path / "last-pass")

    def failing_pass(settings_used, reporter):
        raise RuntimeError("can't reach the database")

    with pytest.raises(RuntimeError):
        clusterer_main.tick(failing_pass, settings, UnhealthyReporter())
    assert check_alive(settings.last_pass_file, max_age_minutes=3)[0]


# ------------------------------------------------------------------ the warning reporter

class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_reporter_warns_once_then_repeats_only_after_the_interval(caplog):
    clock = FakeClock()
    reporter = UnhealthyReporter(repeat_seconds=300, clock=clock)
    unhealthy = HealthReport(problems=["the last 10 summaries all failed"])

    with caplog.at_level(logging.INFO, logger="clusterer"):
        reporter.report(unhealthy)             # starts: warn
        clock.now = 10
        reporter.report(unhealthy)             # 10 s later: quiet
        clock.now = 300
        reporter.report(unhealthy)             # 5 min later: warn again
        reporter.report(HealthReport())        # clears: one "healthy again"
        reporter.report(HealthReport())        # still healthy: quiet

    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    infos = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert warnings == [f"{UNHEALTHY_PREFIX} the last 10 summaries all failed"] * 2
    assert infos == ["clustering healthy again"]


# ------------------------------------------------------------------ the command

def test_main_alive_exits_0_after_a_recent_pass(tmp_path, monkeypatch, capsys):
    path = tmp_path / "last-pass"
    record_pass(path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("CLUSTERER_LAST_PASS_FILE", str(path))
    assert main(["--alive"]) == 0
    assert capsys.readouterr().out.startswith("alive:")


def test_main_alive_exits_1_before_any_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("CLUSTERER_LAST_PASS_FILE", str(tmp_path / "missing"))
    assert main(["--alive"]) == 1
    assert "unhealthy: no clusterer pass has finished yet" in capsys.readouterr().out


def test_main_exits_1_when_the_database_is_unreachable(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:nothing@127.0.0.1:1/nothing")
    assert main([]) == 1
    assert f"{UNHEALTHY_PREFIX} can't reach the database" in capsys.readouterr().out


def test_main_exits_1_on_invalid_settings(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("CLUSTER_ASSIGN_THRESHOLD", "3")
    assert main(["--alive"]) == 1
    assert "CLUSTER_ASSIGN_THRESHOLD must be between 0 and 1" in capsys.readouterr().out
