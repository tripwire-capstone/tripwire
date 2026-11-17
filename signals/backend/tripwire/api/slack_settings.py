"""Manage Slack settings for a resolved project."""

import os

from .. import crypto
from ..slack import parse_slack_webhook_url


def get_slack_status(conn, project_id: int) -> dict:
    """Whether Slack is connected, and how alerts since connecting have gone."""
    enabled_at, verified_at, sent, retrying, failed, last_error, last_failed_at = conn.execute(
        """
        SELECT p.slack_enabled_at, p.slack_verified_at,
               count(*) FILTER (WHERE a.status = 'sent'),
               count(*) FILTER (WHERE a.status = 'pending'),
               count(*) FILTER (WHERE a.status = 'failed'),
               (array_agg(a.last_error ORDER BY a.attempted_at DESC)
                    FILTER (WHERE a.status = 'failed'))[1],
               max(a.attempted_at) FILTER (WHERE a.status = 'failed')
        FROM projects AS p
        LEFT JOIN slack_notification_attempts AS a
               ON a.project_id = p.id AND a.attempted_at >= p.slack_enabled_at
        WHERE p.id = %s
        GROUP BY p.id
        """,
        (project_id,),
    ).fetchone()

    return {
        "configured": enabled_at is not None,
        "enabled_at": enabled_at,
        "verified_at": verified_at,
        "sent": sent,
        "retrying": retrying,
        "failed": failed,
        "last_error": last_error,
        "last_failed_at": last_failed_at,
    }


def save_slack_webhook(conn, project_id: int, webhook_url: str, environ=os.environ) -> dict:
    """Store the webhook encrypted with TRIPWIRE_SECRET_KEY (crypto.py, like the saved API keys).

    Raises ValueError for a URL that isn't a Slack incoming webhook, and crypto.SecretKeyError when
    TRIPWIRE_SECRET_KEY is missing or malformed.
    """
    url = parse_slack_webhook_url(webhook_url)
    if url is None:
        raise ValueError("Enter a valid Slack incoming webhook URL.")

    encrypted_url = crypto.encrypt(url.geturl(), environ)

    (enabled_at,) = conn.execute(
        """
        UPDATE projects
        SET slack_webhook_url_encrypted = %s,
            slack_enabled_at = now(),
            slack_verified_at = NULL
        WHERE id = %s
        RETURNING slack_enabled_at
        """,
        (encrypted_url, project_id),
    ).fetchone()

    return {"configured": True, "enabled_at": enabled_at, "verified_at": None}


def disconnect_slack(conn, project_id: int) -> dict:
    conn.execute(
        """
        UPDATE projects
        SET slack_webhook_url_encrypted = NULL,
            slack_enabled_at = NULL,
            slack_verified_at = NULL
        WHERE id = %s
        """,
        (project_id,),
    )

    return {"configured": False, "enabled_at": None, "verified_at": None}
