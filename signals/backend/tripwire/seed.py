"""Seed the database for the skeleton: python -m tripwire.seed

Creates, once (running it again changes nothing):
  * one project, pointing at the Laminar from .env
  * one signal, "User frustration", with its version 1

No clusters: the real clusterer makes them from events. (The seed used to add a fake
"Placeholder cluster"; it showed in the UI and real assign could pick it. Placeholder clusterer
mode now leaves every event in the pool unless a test adds a cluster itself.)

Settings (env or .env): DATABASE_URL, LMNR_API_URL (default Laminar Cloud), LMNR_UI_URL,
LMNR_PROJECT_ID (optional, for "Open in Laminar" links), and JUDGE_DEFAULT_MODEL
(required when seeding a new project).

The project starts with no saved keys: the workers use LMNR_PROJECT_API_KEY and OPENAI_API_KEY
from their environment until someone saves keys on the Settings page (tripwire/project_keys.py).
"""

import json
import logging
import os

import psycopg
from dotenv import load_dotenv

from .judge.config import configured_judge_model

log = logging.getLogger("seed")

PROJECT_NAME = "default"

SIGNAL_NAME = "User frustration"
INSTRUCTION = ("Flag the trace if the user shows frustration with the assistant: repeating "
               "themselves, correcting it, complaining, or giving up.")
OUTPUT_SCHEMA = [{"name": "reason", "type": "string",
                  "description": "Why the user seems frustrated", "nullable": False}]


def seed(conn, *, api_url, ui_url, laminar_project_id=None):
    """Insert the seed rows unless the project already exists. Returns True if it seeded."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT 1 FROM projects WHERE name = %s", (PROJECT_NAME,))
        if cur.fetchone():
            return False
        model = configured_judge_model()
        cur.execute(
            """INSERT INTO projects (name, laminar_api_url, laminar_ui_url, laminar_project_id)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (PROJECT_NAME, api_url, ui_url, laminar_project_id or None),
        )
        project_id = cur.fetchone()[0]
        cur.execute("INSERT INTO signals (project_id, name) VALUES (%s, %s) RETURNING id",
                    (project_id, SIGNAL_NAME))
        signal_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
               VALUES (%s, 1, %s, %s, %s)""",
            (signal_id, INSTRUCTION, json.dumps(OUTPUT_SCHEMA), model),
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
    log.info("seeded project and signal" if done else "already seeded; nothing to do")


if __name__ == "__main__":
    main()
