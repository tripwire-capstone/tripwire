"""Slack settings: connect a channel's incoming webhook, see how alerts went, disconnect.

    GET    /api/settings/slack   connected or not, a hint of the webhook (never the URL), alert counts
    PUT    /api/settings/slack   send a connect message to the webhook; save it only if Slack accepts
    DELETE /api/settings/slack   say goodbye in the channel, then forget the webhook

The webhook is stored encrypted with TRIPWIRE_SECRET_KEY (tripwire/crypto.py), like the saved API keys.
Connecting checks the webhook the only way Slack allows: by posting to it. So a typo or a revoked
webhook never replaces a working one. The connect message also says how many critical events
Tripwire found in the last 24 hours: those came before connecting, so they never get their own alert.
Replacing a webhook, or disconnecting, posts a short goodbye to the old channel (best effort: a dead
webhook can't be told anything), so nobody waits there for alerts that moved.

The functions below are also used by `python -m tripwire.slack_setup`.
"""

import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from .. import crypto
from ..slack import SlackResult, parse_slack_webhook_url, post_slack
from .db import get_conn

log = logging.getLogger("api.slack_settings")
router = APIRouter(prefix="/api/settings/slack")

DEFAULT_UI_URL = "http://localhost:5174"
MAX_URL_LENGTH = 500
RECENT_SIGNALS_SHOWN = 5


class SlackRefused(Exception):
    """Slack answered the connect message with an error: the webhook is wrong, revoked or archived."""


class SlackUnavailable(Exception):
    """Slack couldn't be reached to check the webhook (outage, timeout, rate limit)."""


# ------------------------------------------------------------------ status

def get_slack_status(conn, project_id: int) -> dict:
    """Whether Slack is connected, and how alerts since connecting have gone."""
    enabled_at, verified_at, sent, retrying, failed, skipped, last_error, last_failed_at = conn.execute(
        """
        SELECT p.slack_enabled_at, p.slack_verified_at,
               count(*) FILTER (WHERE a.status = 'sent'),
               count(*) FILTER (WHERE a.status = 'pending'),
               count(*) FILTER (WHERE a.status = 'failed'),
               count(*) FILTER (WHERE a.status = 'skipped'),
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
        "skipped": skipped,  # repeats in a cluster that already alerted ("skip similar events")
        "last_error": last_error,
        "last_failed_at": last_failed_at,
    }


def webhook_hint(url: str) -> str:
    """Enough to tell two webhooks apart, never enough to post to one: the token's last 4 characters."""
    return f"hooks.slack.com/…{url.rstrip('/')[-4:]}"


def stored_webhook(conn, project_id: int, environ=os.environ) -> tuple[str | None, bool]:
    """(the saved webhook URL or None, whether a saved one can't be decrypted)."""
    (encrypted,) = conn.execute("SELECT slack_webhook_url_encrypted FROM projects WHERE id = %s",
                                (project_id,)).fetchone()
    if encrypted is None:
        return None, False
    try:
        return crypto.decrypt(encrypted, environ), False
    except (crypto.SecretKeyError, crypto.UnreadableError, UnicodeDecodeError):
        return None, True


# ------------------------------------------------------------------ messages

def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


RECENT_CRITICAL_SQL = """
SELECT s.id, s.name, count(*) AS events
FROM signal_events AS e
JOIN signal_versions AS v ON v.id = e.signal_version_id
JOIN signals AS s ON s.id = v.signal_id
WHERE s.project_id = %s AND e.severity = 'critical' AND e.created_at >= now() - interval '24 hours'
GROUP BY s.id, s.name
ORDER BY events DESC, s.id
"""


def connect_message(conn, project_id: int, ui_url: str) -> str:
    lines = [":white_check_mark: *Tripwire is connected to this channel.* "
             "You'll get an alert for each new critical problem and each new cluster."]
    recent = conn.execute(RECENT_CRITICAL_SQL, (project_id,)).fetchall()
    if recent:
        total = sum(events for _, _, events in recent)
        lines.append(f"Before connecting, Tripwire found {_plural(total, 'critical event')} in the last "
                     "24 hours. They won't get their own alerts:")
        for signal_id, name, events in recent[:RECENT_SIGNALS_SHOWN]:
            name = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"• <{ui_url}/signals/{signal_id}?severity=critical&pastHours=24|{name}>: {events}")
        if len(recent) > RECENT_SIGNALS_SHOWN:
            lines.append(f"• and {_plural(len(recent) - RECENT_SIGNALS_SHOWN, 'more signal')}")
    return "\n".join(lines)


GOODBYE_MOVED = ":wave: Tripwire alerts moved to another channel. Nothing more will be posted here."
GOODBYE_DISCONNECTED = ":wave: Tripwire alerts were turned off. Nothing more will be posted here."

# What Slack's error codes mean, in the user's words (https://api.slack.com/messaging/webhooks).
SLACK_ERRORS = {
    "no_service": "this webhook was removed or revoked in Slack",
    "invalid_token": "this webhook was removed or revoked in Slack",
    "no_team": "the Slack workspace for this webhook no longer exists",
    "channel_not_found": "the webhook's channel was deleted",
    "channel_is_archived": "the webhook's channel is archived",
    "action_prohibited": "a Slack admin blocked this webhook",
    "posting_to_general_channel_denied": "only admins can post to this channel",
}


def _refusal(error: str | None) -> str:
    error = error or "unknown error"
    meaning = next((text for code, text in SLACK_ERRORS.items() if code in error), None)
    reason = f"{meaning} ({error})" if meaning else error
    return f"Slack refused the connect message: {reason}. Create a new webhook in Slack and paste it here."


# ------------------------------------------------------------------ connecting and disconnecting

def save_slack_webhook(conn, project_id: int, webhook_url: str, environ=os.environ) -> dict:
    """Store the webhook encrypted with TRIPWIRE_SECRET_KEY, without posting to it.

    Raises ValueError for a URL that isn't a Slack incoming webhook, and crypto.SecretKeyError when
    TRIPWIRE_SECRET_KEY is missing or malformed. Alerts start with runs and clusters after now.
    """
    url = parse_slack_webhook_url(webhook_url)
    if url is None or len(webhook_url) > MAX_URL_LENGTH:
        raise ValueError("Enter a valid Slack incoming webhook URL (https://hooks.slack.com/services/…).")

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


def connect_slack(conn, project_id: int, webhook_url: str, *, ui_url: str = DEFAULT_UI_URL,
                  environ=os.environ, post=post_slack) -> dict:
    """Post the connect message, and save the webhook only if Slack accepts it.

    Raises ValueError (not a Slack webhook URL), crypto.SecretKeyError (TRIPWIRE_SECRET_KEY unusable,
    checked before anything is posted), SlackRefused or SlackUnavailable. When a different webhook
    was connected, its channel gets a goodbye.
    """
    url = parse_slack_webhook_url(webhook_url)
    if url is None or len(webhook_url) > MAX_URL_LENGTH:
        raise ValueError("Enter a valid Slack incoming webhook URL (https://hooks.slack.com/services/…).")
    new_url = url.geturl()
    crypto.encrypt("", environ)  # an unusable key fails here, before the channel hears anything

    result: SlackResult = post(connect_message(conn, project_id, ui_url.rstrip("/")), webhook_url=new_url)
    if not result.ok:
        if result.retryable:
            raise SlackUnavailable(result.error or "no answer")
        raise SlackRefused(_refusal(result.error))

    old_url, _ = stored_webhook(conn, project_id, environ)
    if old_url and old_url != new_url:
        _say_goodbye(old_url, GOODBYE_MOVED, post)

    with conn.transaction():
        save_slack_webhook(conn, project_id, new_url, environ)
        # The connect message arrived, so this webhook is known to work.
        conn.execute("UPDATE projects SET slack_verified_at = now() WHERE id = %s", (project_id,))
    log.info("connected Slack (%s)", webhook_hint(new_url))
    return get_slack_status(conn, project_id)


def disconnect_slack(conn, project_id: int, *, environ=os.environ, post=None) -> dict:
    """Forget the webhook. With `post`, say goodbye in its channel first."""
    if post is not None:
        old_url, _ = stored_webhook(conn, project_id, environ)
        if old_url:
            _say_goodbye(old_url, GOODBYE_DISCONNECTED, post)
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


def _say_goodbye(url: str, text: str, post) -> None:
    result = post(text, webhook_url=url)
    if not result.ok:  # e.g. the old webhook was already revoked: nothing more to do
        log.warning("couldn't post the goodbye to the old Slack channel: %s", result.error)


# ------------------------------------------------------------------ routes

class WebhookIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webhook_url: str


class SlackOut(BaseModel):
    configured: bool
    hint: str | None              # never the URL: it is the secret
    unreadable: bool              # saved, but can't be decrypted: connect again
    enabled_at: datetime | None
    verified_at: datetime | None  # when a message last proved the webhook works
    sent: int                     # alerts since connecting
    retrying: int
    failed: int
    skipped: int                  # repeats of a problem that already alerted
    last_error: str | None
    last_failed_at: datetime | None


def get_slack_post():
    """How messages reach Slack. API tests replace this with a fake."""
    return post_slack


def get_ui_url():
    return (os.getenv("TRIPWIRE_UI_URL", "").strip() or DEFAULT_UI_URL).rstrip("/")


def _project_id(conn) -> int:
    rows = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 2").fetchall()
    if not rows:
        raise HTTPException(503, "no Laminar project yet: run the seed first")
    if len(rows) > 1:
        log.error("more than one row in projects; the API supports one project")
        raise HTTPException(500, "server misconfigured")
    return rows[0][0]


def _out(conn, project_id: int) -> SlackOut:
    url, unreadable = stored_webhook(conn, project_id)
    return SlackOut(**get_slack_status(conn, project_id), hint=webhook_hint(url) if url else None,
                    unreadable=unreadable)


@router.get("", response_model=SlackOut)
def read_slack(conn=Depends(get_conn)):
    return _out(conn, _project_id(conn))


@router.put("", response_model=SlackOut)
def set_slack(body: WebhookIn, conn=Depends(get_conn), post=Depends(get_slack_post),
              ui_url=Depends(get_ui_url)):
    project_id = _project_id(conn)
    try:
        connect_slack(conn, project_id, body.webhook_url, ui_url=ui_url, post=post)
    except ValueError as error:
        raise HTTPException(422, [str(error)])
    except crypto.SecretKeyError as error:
        raise HTTPException(503, str(error))
    except SlackRefused as error:
        raise HTTPException(422, str(error))
    except SlackUnavailable as error:
        raise HTTPException(502, f"Couldn't reach Slack, so the webhook wasn't saved ({error}). Try again.")
    return _out(conn, project_id)


@router.delete("", response_model=SlackOut)
def remove_slack(conn=Depends(get_conn), post=Depends(get_slack_post)):
    project_id = _project_id(conn)
    disconnect_slack(conn, project_id, post=post)
    log.info("disconnected Slack")
    return _out(conn, project_id)
