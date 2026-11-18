"""Seed the database for the skeleton: python -m tripwire.seed

Creates, once (running it again changes nothing):
  * one project, pointing at the Laminar from .env

No signals: a new install starts empty, and the user creates signals on the New signal form
(blank, or from a template in tripwire/signal_templates/). No clusters either: the real
clusterer makes them from events.

Settings (env or .env): DATABASE_URL, LMNR_API_URL (default Laminar Cloud), LMNR_UI_URL and
LMNR_PROJECT_ID (optional, for "Open in Laminar" links).

The project starts with no saved keys: the workers use LMNR_PROJECT_API_KEY and OPENAI_API_KEY
from their environment until someone saves keys on the Settings page (tripwire/project_keys.py).
"""

import logging
import os

import psycopg
from dotenv import load_dotenv

log = logging.getLogger("seed")

PROJECT_NAME = "default"


def seed(conn, *, api_url, ui_url, laminar_project_id=None):
    """Insert the project unless it already exists. Returns True if it seeded."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT 1 FROM projects WHERE name = %s", (PROJECT_NAME,))
        if cur.fetchone():
            return False
        cur.execute(
            """INSERT INTO projects (name, laminar_api_url, laminar_ui_url, laminar_project_id)
               VALUES (%s, %s, %s, %s)""",
            (PROJECT_NAME, api_url, ui_url, laminar_project_id or None),
        )
    return True


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        done = seed(conn,
                    api_url=os.environ.get("LMNR_API_URL", "https://api.lmnr.ai"),
                    ui_url=os.environ.get("LMNR_UI_URL", "https://laminar.sh"),
                    laminar_project_id=os.environ.get("LMNR_PROJECT_ID"))
    log.info("seeded the project" if done else "already seeded; nothing to do")


if __name__ == "__main__":
    main()
