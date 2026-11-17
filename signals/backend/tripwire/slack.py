"""Send best-effort notifications to a Slack incoming webhook."""

import json
import logging
import os
from http.client import HTTPException, HTTPSConnection
from urllib.parse import SplitResult, urlsplit

log = logging.getLogger(__name__)


def parse_slack_webhook_url(value: str) -> SplitResult | None:
    try:
        url = urlsplit(value.strip())
    except ValueError:
        return None

    if (
        url.scheme != "https"
        or url.netloc != "hooks.slack.com"
        or not url.path.startswith("/services/")
        or url.query
        or url.fragment
    ):
        return None

    return url


def post_slack(
    text: str,
    *,
    record_type: str,
    record_id: int,
    webhook_url: str | None = None,
) -> bool:
    if webhook_url is None:
        webhook_url = os.getenv("TRIPWIRE_SLACK_WEBHOOK_URL", "")

    webhook_url = webhook_url.strip()
    if not webhook_url:
        return False

    url = parse_slack_webhook_url(webhook_url)
    if url is None:
        log.warning("Invalid Slack webhook URL for %s %s", record_type, record_id)
        return False

    connection = HTTPSConnection("hooks.slack.com", timeout=2)
    try:
        connection.request(
            "POST",
            url.path,
            body=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with connection.getresponse() as response:
            if response.status == 200:
                return True
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

    return False
