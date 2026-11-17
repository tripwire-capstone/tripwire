"""Send messages to a Slack incoming webhook, and say whether a failure is worth retrying."""

import json
import logging
from dataclasses import dataclass
from http.client import HTTPException, HTTPSConnection
from urllib.parse import SplitResult, urlsplit

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class SlackResult:
    ok: bool
    retryable: bool = False         # worth trying again later (outage, timeout, rate limit)
    error: str | None = None        # short reason, safe to store and show: never the URL
    retry_after: float | None = None  # seconds Slack asked us to wait (HTTP 429)


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


def post_slack(text: str, *, webhook_url: str) -> SlackResult:
    """Post one message. Slack answers 200 "ok", or an error code in the body (e.g. no_service).

    Retryable: network errors, timeouts, 429 and 5xx. Not retryable: a bad URL and other 4xx,
    which mean the webhook was revoked, the channel archived, or the message rejected.
    """
    url = parse_slack_webhook_url(webhook_url)
    if url is None:
        return SlackResult(ok=False, error="invalid webhook URL")

    connection = HTTPSConnection("hooks.slack.com", timeout=TIMEOUT_SECONDS)
    try:
        connection.request(
            "POST",
            url.path,
            body=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with connection.getresponse() as response:
            if response.status == 200:
                return SlackResult(ok=True)
            body = response.read(200).decode("utf-8", "replace").strip()
            error = f"HTTP {response.status}" + (f": {body}" if body else "")
            retry_after = None
            if response.status == 429:
                try:
                    retry_after = float(response.getheader("Retry-After", ""))
                except ValueError:
                    pass
            return SlackResult(ok=False, retryable=response.status == 429 or response.status >= 500,
                               error=error, retry_after=retry_after)
    except (OSError, HTTPException) as error:
        return SlackResult(ok=False, retryable=True, error=type(error).__name__)
    finally:
        connection.close()
