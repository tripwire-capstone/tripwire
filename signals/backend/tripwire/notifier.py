"""Poll committed Tripwire records and send Slack alerts, retrying failed sends.

Alerts:
  * critical_run: a finished run with at least one critical finding. Shows the finding, with links
    to it in Tripwire and to the trace in Laminar (when the project's Laminar ID is set).
    "Skip similar events" (mock-ui/SPEC.md): only the first critical event in a cluster alerts. The
    notifier waits up to CLUSTER_WAIT_SECONDS after the run finishes for the clusterer to place the
    event, then decides:
      - in a cluster that already had a critical alert since Slack was connected: skipped (logged
        with that cluster, never sent)
      - in another cluster, or its summary failed, or the wait is over: sent
      - embedded POOL_SETTLE_SECONDS ago and still in no cluster: sent (left in the pool: new)
      - not placed yet: looked at again on the next poll
    The pool check relies on the clusterer's pass order (loop.run_pass): prepare saves the
    embedding, then assign tries to place it a few seconds later in the same pass. If a pass ever
    takes longer than POOL_SETTLE_SECONDS between the two, a repeat can be sent (an extra message,
    never a lost one). Tell the notifier's owner if that order changes.
    A stuck clusterer (or no OpenAI key) only delays an alert by the wait; it never hides one.
  * new_cluster: a new cluster, once it has a name (or after 30 s). Shows its name, which is the
    summary of a typical finding, how many findings and conversations it has, and a link.

Delivery (slack_notification_attempts, one row per alert):
  * An alert is claimed before it is sent, so two notifiers never send it at the same time.
  * A failed send is retried after 30 s, 2 min, 10 min and 30 min (or later, if Slack asks), then
    given up as failed. Slack refusing the message (revoked webhook, archived channel) fails at once.
  * A notifier that dies mid-send leaves the claim to expire; the alert is then tried again, so it
    can (rarely) arrive twice. Dropping a critical alert is worse than a duplicate.
  * Catch-up note: after alerts failed for good, the next alert that gets through is followed by one
    note saying how many were missed (each failed alert is counted in one note, marked caught_up_at).

Settings (env): DATABASE_URL, TRIPWIRE_SECRET_KEY, TRIPWIRE_UI_URL (links; default
http://localhost:5174), TRIPWIRE_SLACK_POLL_INTERVAL_SECONDS (default 15).
"""

import logging
import math
import os
import signal
from html import escape
from threading import Event

import psycopg
from psycopg.rows import dict_row

from . import crypto
from .slack import SlackResult, post_slack

log = logging.getLogger(__name__)

DEFAULT_UI_URL = "http://localhost:5174"
RETRY_DELAYS = (30, 120, 600, 1800)   # seconds to wait before tries 2, 3, 4 and 5
MAX_TRIES = len(RETRY_DELAYS) + 1
CLAIM_SECONDS = 60                    # a claimed alert is tried again after this if never resolved
MAX_FINDING_CHARS = 300
CLUSTER_WAIT_SECONDS = 60             # longest a critical alert waits for its event to be clustered
POOL_SETTLE_SECONDS = 30              # embedded this long ago and in no cluster: left in the pool


def _not_handled(kind: str, record: str) -> str:
    """SQL: no alert row for this record yet, or one waiting for a retry that is now due."""
    return f"""NOT EXISTS (
        SELECT 1
        FROM slack_notification_attempts AS a
        WHERE a.notification_type = '{kind}' AND a.record_id = {record}
          AND NOT (a.status = 'pending' AND a.next_try_at <= now())
    )"""


CRITICAL_RUNS_SQL = f"""
SELECT r.id AS run_id, s.id AS signal_id, s.name AS signal_name,
       first.id AS event_id, first.payload, first.description, v.output_schema,
       (SELECT count(*) FROM signal_events AS c
        WHERE c.run_id = r.id AND c.severity = 'critical') AS critical_count,
       t.laminar_trace_id, p.laminar_ui_url, p.laminar_project_id,
       p.id AS project_id, p.slack_webhook_url_encrypted,
       (SELECT m.cluster_id FROM cluster_members AS m WHERE m.event_id = first.id LIMIT 1) AS cluster_id,
       (SELECT es.status FROM event_summaries AS es WHERE es.event_id = first.id) AS summary_status,
       EXISTS (SELECT 1 FROM event_embeddings AS ee
               WHERE ee.event_id = first.id
                 AND ee.created_at <= now() - make_interval(secs => %(pool_settle)s)) AS left_in_pool,
       (%(cluster_wait)s::float8 IS NULL
        OR r.finished_at <= now() - make_interval(secs => %(cluster_wait)s::float8)) AS waited_enough,
       EXISTS (SELECT 1 FROM slack_notification_attempts AS a
               WHERE a.notification_type = 'critical_run' AND a.record_id = r.id) AS decided
FROM signal_runs AS r
JOIN traces AS t ON t.id = r.trace_id
JOIN signal_versions AS v ON v.id = r.signal_version_id
JOIN signals AS s ON s.id = v.signal_id
JOIN projects AS p ON p.id = s.project_id
CROSS JOIN LATERAL (
    SELECT e.id, e.payload, e.description
    FROM signal_events AS e
    WHERE e.run_id = r.id AND e.severity = 'critical'
    ORDER BY e.finding_index, e.id
    LIMIT 1
) AS first
WHERE r.status = 'done'
  AND p.slack_webhook_url_encrypted IS NOT NULL
  AND r.finished_at >= p.slack_enabled_at
  AND {_not_handled('critical_run', 'r.id')}
ORDER BY r.finished_at, r.id
LIMIT %(batch_size)s
"""

NEW_CLUSTERS_SQL = f"""
SELECT c.id AS cluster_id, s.id AS signal_id, s.name AS signal_name,
       NULLIF(btrim(c.name), '') AS cluster_name,
       count(m.event_id) AS event_count,
       count(DISTINCT coalesce(t.session_id, t.id::text)) AS conversation_count,
       p.id AS project_id, p.slack_webhook_url_encrypted
FROM clusters AS c
JOIN signals AS s ON s.id = c.signal_id
JOIN projects AS p ON p.id = s.project_id
LEFT JOIN cluster_members AS m ON m.cluster_id = c.id
LEFT JOIN signal_events AS e ON e.id = m.event_id
LEFT JOIN traces AS t ON t.id = e.trace_id
WHERE p.slack_webhook_url_encrypted IS NOT NULL
  AND c.created_at >= p.slack_enabled_at
  AND (
      NULLIF(btrim(c.name), '') IS NOT NULL
      OR c.created_at <= now() - interval '30 seconds'
  )
  AND {_not_handled('new_cluster', 'c.id')}
GROUP BY c.id, c.created_at, c.name, s.id, s.name, p.id, p.slack_webhook_url_encrypted
ORDER BY c.created_at, c.id
LIMIT %(batch_size)s
"""

# Insert the alert's row, or take a due retry. Nothing comes back when the alert is already sent,
# failed, or claimed by someone else, or the project's webhook changed since it was read.
CLAIM_SQL = """
INSERT INTO slack_notification_attempts AS a
    (notification_type, record_id, project_id, next_try_at)
SELECT %(kind)s, %(record_id)s, p.id, now() + make_interval(secs => %(claim)s)
FROM projects AS p
WHERE p.id = %(project_id)s AND p.slack_webhook_url_encrypted = %(webhook)s
ON CONFLICT (notification_type, record_id) DO UPDATE
    SET tries = a.tries + 1, next_try_at = EXCLUDED.next_try_at, project_id = EXCLUDED.project_id
    WHERE a.status = 'pending' AND a.next_try_at <= now()
RETURNING a.tries
"""


# Another critical run in this cluster already alerted (or is being sent) since Slack was connected.
# A failed alert doesn't count: the team never saw it.
ALREADY_ALERTED_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM cluster_members AS m
    JOIN signal_events AS e ON e.id = m.event_id AND e.severity = 'critical'
    JOIN slack_notification_attempts AS a
      ON a.notification_type = 'critical_run' AND a.record_id = e.run_id
    JOIN projects AS p ON p.id = %(project_id)s
    WHERE m.cluster_id = %(cluster_id)s
      AND e.run_id <> %(run_id)s
      AND a.status IN ('sent', 'pending')
      AND a.attempted_at >= p.slack_enabled_at
)
"""

SKIP_SQL = """
INSERT INTO slack_notification_attempts
    (notification_type, record_id, project_id, status, cluster_id)
VALUES ('critical_run', %(run_id)s, %(project_id)s, 'skipped', %(cluster_id)s)
ON CONFLICT (notification_type, record_id) DO NOTHING
"""


def find_critical_runs(conn, batch_size=50, cluster_wait=CLUSTER_WAIT_SECONDS):
    return conn.cursor(row_factory=dict_row).execute(
        CRITICAL_RUNS_SQL, {"batch_size": batch_size, "cluster_wait": cluster_wait,
                            "pool_settle": POOL_SETTLE_SECONDS}).fetchall()


def critical_decision(conn, row) -> str:
    """'send', 'skip' (a repeat in a cluster that already alerted) or 'wait' (not clustered yet)."""
    if row["decided"]:  # a retry: it was already decided to send
        return "send"
    if row["cluster_id"] is not None:
        (repeat,) = conn.execute(ALREADY_ALERTED_SQL, {
            "cluster_id": row["cluster_id"], "run_id": row["run_id"],
            "project_id": row["project_id"]}).fetchone()
        return "skip" if repeat else "send"
    if row["summary_status"] == "failed" or row["left_in_pool"] or row["waited_enough"]:
        return "send"
    return "wait"


def find_new_clusters(conn, batch_size=50):
    return conn.cursor(row_factory=dict_row).execute(
        NEW_CLUSTERS_SQL, {"batch_size": batch_size}).fetchall()


# ------------------------------------------------------------------ messages


def finding_text(payload: dict | None, output_schema: list | None, description: str | None) -> str:
    """The finding in words: the signal's first text field the judge filled, else any text."""
    payload = payload or {}
    for spec in output_schema or []:
        value = payload.get(spec.get("name"))
        if spec.get("type") == "string" and isinstance(value, str) and value.strip():
            return value
    for value in payload.values():
        if isinstance(value, str) and value.strip():
            return value
    return description or ""


def shorten(text: str, limit: int = MAX_FINDING_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if text[limit - 1] != " " and " " in cut:  # the cut splits a word: drop that word
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def _escape(text: str) -> str:
    """Slack mrkdwn treats &, < and > as markup."""
    return escape(text, quote=False)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def critical_message(row: dict, ui_url: str) -> str:
    count = row["critical_count"]
    head = f":rotating_light: *Critical* in *{_escape(row['signal_name'])}*"
    if count > 1:
        head += f" · {count} critical findings in one turn"
    lines = [head]
    text = shorten(finding_text(row["payload"], row["output_schema"], row["description"]))
    if text:
        lines.append(f"> {_escape(text)}")
    links = [f"<{ui_url}/signals/{row['signal_id']}?event={row['event_id']}&severity=critical"
             f"|Open in Tripwire>"]
    if row["laminar_project_id"]:
        base = row["laminar_ui_url"].rstrip("/")
        links.append(f"<{base}/project/{row['laminar_project_id']}/traces/{row['laminar_trace_id']}"
                     f"|View trace in Laminar>")
    lines.append(" · ".join(links))
    return "\n".join(lines)


def cluster_message(row: dict, ui_url: str) -> str:
    name = row["cluster_name"] or f"Unnamed cluster #{row['cluster_id']}"
    return "\n".join([
        f":jigsaw: *New cluster* in *{_escape(row['signal_name'])}* · "
        f"{_plural(row['event_count'], 'finding')} in {_plural(row['conversation_count'], 'conversation')}",
        f"> {_escape(shorten(name))}",
        f"<{ui_url}/signals/{row['signal_id']}?cluster={row['cluster_id']}|Open cluster in Tripwire>",
    ])


# ------------------------------------------------------------------ delivery


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


def claim(conn, kind, record_id, *, project_id, encrypted_webhook) -> int | None:
    """Take the alert for one try. Returns which try this is, or None if it isn't ours to send."""
    row = conn.execute(CLAIM_SQL, {"kind": kind, "record_id": record_id, "claim": CLAIM_SECONDS,
                                   "project_id": project_id, "webhook": encrypted_webhook}).fetchone()
    return row[0] if row else None


def record_result(conn, kind, record_id, tries: int, result: SlackResult) -> None:
    key = (kind, record_id)
    if result.ok:
        conn.execute("""
            UPDATE slack_notification_attempts
            SET status = 'sent', sent_at = now(), next_try_at = NULL, last_error = NULL
            WHERE notification_type = %s AND record_id = %s
        """, key)
        log.info("Sent Slack alert for %s %s (try %s)", kind, record_id, tries)
    elif result.retryable and tries < MAX_TRIES:
        delay = max(RETRY_DELAYS[tries - 1], result.retry_after or 0)
        conn.execute("""
            UPDATE slack_notification_attempts
            SET next_try_at = now() + make_interval(secs => %s), last_error = %s
            WHERE notification_type = %s AND record_id = %s
        """, (delay, result.error, *key))
        log.warning("Slack alert for %s %s failed (%s); retrying in %s s (try %s of %s)",
                    kind, record_id, result.error, delay, tries, MAX_TRIES)
    else:
        conn.execute("""
            UPDATE slack_notification_attempts
            SET status = 'failed', next_try_at = NULL, last_error = %s
            WHERE notification_type = %s AND record_id = %s
        """, (result.error, *key))
        log.error("Slack alert for %s %s failed for good after %s %s: %s",
                  kind, record_id, tries, "try" if tries == 1 else "tries", result.error)


# Failed alerts since connecting that no catch-up note has counted yet, marked as counted now.
CATCH_UP_SQL = """
UPDATE slack_notification_attempts AS a
SET caught_up_at = now()
FROM projects AS p
WHERE p.id = %(project_id)s AND a.project_id = p.id
  AND a.status = 'failed' AND a.caught_up_at IS NULL AND a.attempted_at >= p.slack_enabled_at
RETURNING a.notification_type, a.record_id
"""

UNDO_CATCH_UP_SQL = """
UPDATE slack_notification_attempts SET caught_up_at = NULL
WHERE (notification_type, record_id) IN (SELECT * FROM unnest(%(kinds)s::text[], %(ids)s::bigint[]))
"""


def catch_up_message(missed: list, ui_url: str) -> str:
    critical = sum(1 for kind, _ in missed if kind == "critical_run")
    clusters = len(missed) - critical
    parts = ([_plural(critical, "critical alert")] if critical else []) + \
            ([_plural(clusters, "new-cluster alert")] if clusters else [])
    verb = "it" if len(missed) == 1 else "they"
    return (f":inbox_tray: *Catching up:* {' and '.join(parts)} couldn't be sent to this channel earlier, "
            f"so {verb} never arrived. <{ui_url}/|Open Tripwire> to see what was found.")


def catch_up(conn, project_id: int, webhook_url: str, ui_url: str) -> None:
    """After a send that worked: one note about alerts that failed for good since connecting."""
    missed = conn.execute(CATCH_UP_SQL, {"project_id": project_id}).fetchall()
    if not missed:
        return
    result = post_slack(catch_up_message(missed, ui_url), webhook_url=webhook_url)
    if result.ok:
        log.info("Sent a Slack catch-up note for %s missed alerts", len(missed))
    else:  # count them in the next note instead
        conn.execute(UNDO_CATCH_UP_SQL, {"kinds": [kind for kind, _ in missed],
                                         "ids": [record_id for _, record_id in missed]})
        log.warning("Slack catch-up note failed (%s); trying again after the next alert", result.error)


def deliver(conn, kind, record_id, text, *, project_id, encrypted_webhook, environ=os.environ,
            ui_url: str = DEFAULT_UI_URL) -> None:
    """Claim, send and record one alert. Use an autocommit connection so the claim is saved first."""
    tries = claim(conn, kind, record_id, project_id=project_id, encrypted_webhook=encrypted_webhook)
    if tries is None:
        return
    try:
        webhook_url = crypto.decrypt(encrypted_webhook, environ)
    except (crypto.UnreadableError, UnicodeDecodeError):
        result = SlackResult(ok=False, error="cannot decrypt the webhook: was TRIPWIRE_SECRET_KEY changed?")
    else:
        result = post_slack(text, webhook_url=webhook_url)
    record_result(conn, kind, record_id, tries, result)
    if result.ok:
        mark_slack_verified(conn, project_id, encrypted_webhook)
        catch_up(conn, project_id, webhook_url, ui_url)


def poll_once(conn, stop, ui_url: str = DEFAULT_UI_URL, environ=os.environ,
              cluster_wait: float | None = CLUSTER_WAIT_SECONDS):
    """One pass over due alerts. cluster_wait=None sends critical alerts without waiting for the
    clusterer (a repeat that is already clustered is still skipped)."""
    for row in find_critical_runs(conn, cluster_wait=cluster_wait):
        if stop.is_set():
            return
        # Decided one run at a time, so a run sent earlier in this poll counts for the next one.
        decision = critical_decision(conn, row)
        if decision == "wait":
            continue
        if decision == "skip":
            conn.execute(SKIP_SQL, {"run_id": row["run_id"], "project_id": row["project_id"],
                                    "cluster_id": row["cluster_id"]})
            log.info("Skipped Slack alert for critical_run %s: cluster %s already alerted",
                     row["run_id"], row["cluster_id"])
            continue
        deliver(conn, "critical_run", row["run_id"], critical_message(row, ui_url),
                project_id=row["project_id"], encrypted_webhook=row["slack_webhook_url_encrypted"],
                environ=environ, ui_url=ui_url)

    for row in find_new_clusters(conn):
        if stop.is_set():
            return
        deliver(conn, "new_cluster", row["cluster_id"], cluster_message(row, ui_url),
                project_id=row["project_id"], encrypted_webhook=row["slack_webhook_url_encrypted"],
                environ=environ, ui_url=ui_url)


def main():
    logging.basicConfig(level=logging.INFO)
    try:
        crypto.encrypt("")  # checks TRIPWIRE_SECRET_KEY now, not at the first alert
    except crypto.SecretKeyError as error:
        raise SystemExit(str(error)) from None

    interval_setting = "TRIPWIRE_SLACK_POLL_INTERVAL_SECONDS"
    try:
        interval = float(os.getenv(interval_setting, "15"))
    except ValueError:
        raise SystemExit(f"{interval_setting} must be a positive finite number") from None
    if not math.isfinite(interval) or interval <= 0:
        raise SystemExit(f"{interval_setting} must be a positive finite number")

    ui_url = (os.getenv("TRIPWIRE_UI_URL", "").strip() or DEFAULT_UI_URL).rstrip("/")
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
                    poll_once(conn, stop, ui_url)
                    stop.wait(interval)
        except psycopg.OperationalError as error:
            log.warning("Slack notifier database connection failed (%s)", type(error).__name__)
            stop.wait(5)


if __name__ == "__main__":
    main()
