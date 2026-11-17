"""Manage Slack settings for a resolved project."""

from cryptography.fernet import Fernet

from ..slack import parse_slack_webhook_url


def get_slack_status(conn, project_id: int) -> dict:
    enabled_at, verified_at = conn.execute(
        """
        SELECT slack_enabled_at, slack_verified_at
        FROM projects
        WHERE id = %s
        """,
        (project_id,),
    ).fetchone()

    return {
        "configured": enabled_at is not None,
        "enabled_at": enabled_at,
        "verified_at": verified_at,
    }


def save_slack_webhook(
    conn, project_id: int, webhook_url: str, cipher: Fernet
) -> dict:
    url = parse_slack_webhook_url(webhook_url)
    if url is None:
        raise ValueError("Enter a valid Slack incoming webhook URL.")

    encrypted_url = cipher.encrypt(url.geturl().encode("utf-8"))

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
