"""Poll committed Tripwire records and send Slack alerts."""

import logging
import math
import os
import signal
from html import escape
from threading import Event

import psycopg

from .slack import post_slack

log = logging.getLogger(__name__)

CRITICAL_RUNS_SQL = """
SELECT r.id, s.name, count(*) AS critical_count
FROM signal_runs AS r
JOIN signal_events AS e ON e.run_id = r.id
JOIN signal_versions AS v ON v.id = r.signal_version_id
JOIN signals AS s ON s.id = v.signal_id
WHERE r.status = 'done'
  AND r.finished_at >= %(enabled_at)s
  AND e.severity = 'critical'
  AND NOT EXISTS (
      SELECT 1
      FROM slack_notification_attempts AS a
      WHERE a.notification_type = 'critical_run' AND a.record_id = r.id
  )
GROUP BY r.id, r.finished_at, s.name
ORDER BY r.finished_at, r.id
LIMIT %(batch_size)s
"""

NEW_CLUSTERS_SQL = """
SELECT c.id, s.name, NULLIF(btrim(c.name), '') AS cluster_name,
       count(m.event_id) AS event_count
FROM clusters AS c
JOIN signals AS s ON s.id = c.signal_id
LEFT JOIN cluster_members AS m ON m.cluster_id = c.id
WHERE c.created_at >= %(enabled_at)s
  AND (
      NULLIF(btrim(c.name), '') IS NOT NULL
      OR c.created_at <= now() - interval '30 seconds'
  )
  AND NOT EXISTS (
      SELECT 1
      FROM slack_notification_attempts AS a
      WHERE a.notification_type = 'new_cluster' AND a.record_id = c.id
  )
GROUP BY c.id, c.created_at, c.name, s.name
ORDER BY c.created_at, c.id
LIMIT %(batch_size)s
"""


def find_critical_runs(conn, enabled_at, batch_size=50):
    return conn.execute(
        CRITICAL_RUNS_SQL,
        {"enabled_at": enabled_at, "batch_size": batch_size},
    ).fetchall()


def find_new_clusters(conn, enabled_at, batch_size=50):
    return conn.execute(
        NEW_CLUSTERS_SQL,
        {"enabled_at": enabled_at, "batch_size": batch_size},
    ).fetchall()


def load_enabled_at(conn):
    conn.execute("""
        INSERT INTO slack_notifier_state (singleton) VALUES (true)
        ON CONFLICT (singleton) DO NOTHING
    """)
    return conn.execute("""
        SELECT enabled_at FROM slack_notifier_state WHERE singleton
    """).fetchone()[0]


def send_once(conn, notification_type, record_id, text):
    """Use an autocommit connection so the attempt is saved before delivery."""
    attempt = conn.execute("""
        INSERT INTO slack_notification_attempts (notification_type, record_id)
        VALUES (%s, %s)
        ON CONFLICT (notification_type, record_id) DO NOTHING
        RETURNING record_id
    """, (notification_type, record_id)).fetchone()
    if attempt is not None:
        post_slack(text, record_type=notification_type, record_id=record_id)


def poll_once(conn, enabled_at, stop):
    for run_id, signal_name, count in find_critical_runs(conn, enabled_at):
        if stop.is_set():
            return
        events = "event" if count == 1 else "events"
        text = (
            f"*Critical* · {escape(signal_name, quote=False)} · "
            f"{count} critical {events} · run #{run_id}"
        )
        send_once(conn, "critical_run", run_id, text)

    if stop.is_set():
        return

    for cluster_id, signal_name, cluster_name, count in find_new_clusters(conn, enabled_at):
        if stop.is_set():
            return
        name = escape(cluster_name or f"Cluster #{cluster_id}", quote=False)
        events = "event" if count == 1 else "events"
        text = f"*New cluster* · {escape(signal_name, quote=False)} · {name} · {count} {events}"
        send_once(conn, "new_cluster", cluster_id, text)


def main():
    logging.basicConfig(level=logging.INFO)
    if not os.getenv("TRIPWIRE_SLACK_WEBHOOK_URL", "").strip():
        log.info("Slack notifier disabled because no webhook is configured")
        return

    interval_setting = "TRIPWIRE_SLACK_POLL_INTERVAL_SECONDS"
    try:
        interval = float(os.getenv(interval_setting, "15"))
    except ValueError:
        raise SystemExit(f"{interval_setting} must be a positive finite number") from None
    if not math.isfinite(interval) or interval <= 0:
        raise SystemExit(f"{interval_setting} must be a positive finite number")

    database_url = os.environ["DATABASE_URL"]
    stop = Event()

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    while not stop.is_set():
        try:
            with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
                enabled_at = load_enabled_at(conn)
                while not stop.is_set():
                    poll_once(conn, enabled_at, stop)
                    stop.wait(interval)
        except psycopg.OperationalError as error:
            log.warning("Slack notifier database connection failed (%s)", type(error).__name__)
            stop.wait(5)


if __name__ == "__main__":
    main()
