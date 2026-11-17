"""Poll committed Tripwire records and send Slack alerts."""

import logging
import math
import os
import signal
from html import escape
from threading import Event

import psycopg
from cryptography.fernet import Fernet, InvalidToken

from .slack import post_slack

log = logging.getLogger(__name__)

CRITICAL_RUNS_SQL = """
SELECT r.id, s.name, count(*) AS critical_count,
       p.id, p.slack_webhook_url_encrypted
FROM signal_runs AS r
JOIN signal_events AS e ON e.run_id = r.id
JOIN signal_versions AS v ON v.id = r.signal_version_id
JOIN signals AS s ON s.id = v.signal_id
JOIN projects AS p ON p.id = s.project_id
WHERE r.status = 'done'
  AND p.slack_webhook_url_encrypted IS NOT NULL
  AND r.finished_at >= p.slack_enabled_at
  AND e.severity = 'critical'
  AND NOT EXISTS (
      SELECT 1
      FROM slack_notification_attempts AS a
      WHERE a.notification_type = 'critical_run' AND a.record_id = r.id
  )
GROUP BY r.id, r.finished_at, s.name, p.id, p.slack_webhook_url_encrypted
ORDER BY r.finished_at, r.id
LIMIT %(batch_size)s
"""

NEW_CLUSTERS_SQL = """
SELECT c.id, s.name, NULLIF(btrim(c.name), '') AS cluster_name,
       count(m.event_id) AS event_count, p.id, p.slack_webhook_url_encrypted
FROM clusters AS c
JOIN signals AS s ON s.id = c.signal_id
JOIN projects AS p ON p.id = s.project_id
LEFT JOIN cluster_members AS m ON m.cluster_id = c.id
WHERE p.slack_webhook_url_encrypted IS NOT NULL
  AND c.created_at >= p.slack_enabled_at
  AND (
      NULLIF(btrim(c.name), '') IS NOT NULL
      OR c.created_at <= now() - interval '30 seconds'
  )
  AND NOT EXISTS (
      SELECT 1
      FROM slack_notification_attempts AS a
      WHERE a.notification_type = 'new_cluster' AND a.record_id = c.id
  )
GROUP BY c.id, c.created_at, c.name, s.name, p.id, p.slack_webhook_url_encrypted
ORDER BY c.created_at, c.id
LIMIT %(batch_size)s
"""


def find_critical_runs(conn, batch_size=50):
    return conn.execute(CRITICAL_RUNS_SQL, {"batch_size": batch_size}).fetchall()


def find_new_clusters(conn, batch_size=50):
    return conn.execute(NEW_CLUSTERS_SQL, {"batch_size": batch_size}).fetchall()


def mark_slack_verified(conn, project_id: int, encrypted_webhook: bytes) -> None:
    conn.execute(
        """
        UPDATE projects
        SET slack_verified_at = now()
        WHERE id = %s
          AND slack_webhook_url_encrypted = %s
          AND slack_verified_at IS NULL
        """,
        (project_id, encrypted_webhook),
    )


def send_once(conn, notification_type, record_id, text, *, project_id, encrypted_webhook, cipher):
    """Use an autocommit connection so the attempt is saved before delivery."""
    try:
        webhook_url = cipher.decrypt(encrypted_webhook).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError):
        log.warning("Unable to decrypt Slack webhook for project %s", project_id)
        return

    attempt = conn.execute("""
        INSERT INTO slack_notification_attempts (notification_type, record_id)
        SELECT %s, %s
        FROM projects
        WHERE id = %s AND slack_webhook_url_encrypted = %s
        ON CONFLICT (notification_type, record_id) DO NOTHING
        RETURNING record_id
    """, (notification_type, record_id, project_id, encrypted_webhook)).fetchone()
    if attempt is None:
        return
    if post_slack(text, record_type=notification_type, record_id=record_id, webhook_url=webhook_url):
        mark_slack_verified(conn, project_id, encrypted_webhook)


def poll_once(conn, cipher, stop):
    for run_id, signal_name, count, project_id, encrypted_webhook in find_critical_runs(conn):
        if stop.is_set():
            return
        events = "event" if count == 1 else "events"
        text = (
            f"*Critical* · {escape(signal_name, quote=False)} · "
            f"{count} critical {events} · run #{run_id}"
        )
        send_once(conn, "critical_run", run_id, text, project_id=project_id,
                  encrypted_webhook=encrypted_webhook, cipher=cipher)

    if stop.is_set():
        return

    for cluster_id, signal_name, cluster_name, count, project_id, encrypted_webhook in find_new_clusters(conn):
        if stop.is_set():
            return
        name = escape(cluster_name or f"Cluster #{cluster_id}", quote=False)
        events = "event" if count == 1 else "events"
        text = f"*New cluster* · {escape(signal_name, quote=False)} · {name} · {count} {events}"
        send_once(conn, "new_cluster", cluster_id, text, project_id=project_id,
                  encrypted_webhook=encrypted_webhook, cipher=cipher)


def main():
    logging.basicConfig(level=logging.INFO)
    try:
        cipher = Fernet(os.environ["TRIPWIRE_SECRET_KEY"])
    except (KeyError, ValueError):
        raise SystemExit("Set TRIPWIRE_SECRET_KEY to a valid Fernet key") from None

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
                while not stop.is_set():
                    poll_once(conn, cipher, stop)
                    stop.wait(interval)
        except psycopg.OperationalError as error:
            log.warning("Slack notifier database connection failed (%s)", type(error).__name__)
            stop.wait(5)


if __name__ == "__main__":
    main()
