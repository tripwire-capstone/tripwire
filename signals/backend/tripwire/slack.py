"""Send best-effort notifications to a Slack incoming webhook."""

import json
import logging
import os
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlsplit

log = logging.getLogger(__name__)


def post_slack(text: str, *, record_type: str, record_id: int) -> None:
    webhook_url = os.getenv("TRIPWIRE_SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        return

    try:
        url = urlsplit(webhook_url)
    except ValueError:
        log.warning("Invalid Slack webhook URL for %s %s", record_type, record_id)
        return

    if (
        url.scheme != "https"
        or url.netloc != "hooks.slack.com"
        or not url.path.startswith("/services/")
        or url.query
        or url.fragment
    ):
        log.warning("Invalid Slack webhook URL for %s %s", record_type, record_id)
        return

    connection = HTTPSConnection("hooks.slack.com", timeout=2)
    try:
        connection.request(
            "POST",
            url.path,
            body=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with connection.getresponse() as response:
            if response.status != 200:
                log.warning(
                    "Slack delivery failed for %s %s (HTTP %s)",
                    record_type,
                    record_id,
                    response.status,
                )
    except (OSError, HTTPException) as error:
        log.warning(
            "Slack delivery failed for %s %s (%s)",
            record_type,
            record_id,
            type(error).__name__,
        )
    finally:
        connection.close()
