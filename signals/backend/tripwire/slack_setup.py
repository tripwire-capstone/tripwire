"""Turn Slack notifications on or off for a project from the command line (the Settings page does the same).

    python -m tripwire.slack_setup connect [WEBHOOK_URL]
    python -m tripwire.slack_setup status
    python -m tripwire.slack_setup disconnect

connect posts a connect message to the webhook and, if Slack accepts it, encrypts the webhook with
TRIPWIRE_SECRET_KEY (tripwire/crypto.py) and stores it on the project. disconnect posts a goodbye
first. Leave WEBHOOK_URL out to read TRIPWIRE_SLACK_WEBHOOK_URL from the environment (or .env),
which keeps the URL out of your shell history. Only alerts for runs and clusters that finish after
connecting are sent; reconnecting starts that clock again.

--project picks the project by name (default: the seeded "default" project).
Settings (env or .env): DATABASE_URL, TRIPWIRE_SECRET_KEY, TRIPWIRE_UI_URL (links in the connect message).
"""

import argparse
import os

import psycopg
from dotenv import load_dotenv

from . import crypto
from .api.slack_settings import (SlackRefused, SlackUnavailable, connect_slack, disconnect_slack,
                                 get_slack_status, get_ui_url)
from .slack import post_slack

DEFAULT_PROJECT = "default"


def find_project(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM projects WHERE name = %s", (name,)).fetchone()
    if row is None:
        raise SystemExit(f"No project named {name!r}. Run the seed first: python -m tripwire.seed")
    return row[0]


def describe(status: dict) -> str:
    if not status["configured"]:
        return "Slack: not connected"
    verified = status["verified_at"] or "not yet (no message delivered since connecting)"
    lines = [f"Slack: connected since {status['enabled_at']}", f"First delivery: {verified}"]
    if "sent" in status:  # alerts since connecting (only status reads these)
        lines.append(f"Alerts: {status['sent']} sent, {status['retrying']} waiting to retry, "
                     f"{status['failed']} failed, {status['skipped']} skipped as repeats")
        if status["failed"]:
            lines.append(f"Last failure: {status['last_failed_at']}: {status['last_error']}")
    return "\n".join(lines)


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m tripwire.slack_setup",
                                     description="Turn Slack notifications on or off.")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="project name (default: %(default)s)")
    commands = parser.add_subparsers(dest="command", required=True)
    connect = commands.add_parser("connect", help="store a Slack incoming webhook for the project")
    connect.add_argument("webhook_url", nargs="?",
                         help="https://hooks.slack.com/services/... (default: $TRIPWIRE_SLACK_WEBHOOK_URL)")
    commands.add_parser("status", help="show whether Slack is connected")
    commands.add_parser("disconnect", help="remove the webhook and stop notifications")
    args = parser.parse_args(argv)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        project_id = find_project(conn, args.project)
        if args.command == "connect":
            webhook_url = args.webhook_url or os.getenv("TRIPWIRE_SLACK_WEBHOOK_URL", "")
            if not webhook_url.strip():
                raise SystemExit("Pass a webhook URL or set TRIPWIRE_SLACK_WEBHOOK_URL")
            try:
                status = connect_slack(conn, project_id, webhook_url, ui_url=get_ui_url())
            except (ValueError, crypto.SecretKeyError, SlackRefused) as error:
                raise SystemExit(str(error)) from None
            except SlackUnavailable as error:
                raise SystemExit(f"Couldn't reach Slack, so the webhook wasn't saved ({error})") from None
        elif args.command == "disconnect":
            status = disconnect_slack(conn, project_id, post=post_slack)
        else:
            status = get_slack_status(conn, project_id)
    print(describe(status))


if __name__ == "__main__":
    main()
