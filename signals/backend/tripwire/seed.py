"""Seed the database for the skeleton: python -m tripwire.seed

Creates, once (running it again changes nothing):
  * one project, pointing at the Laminar from .env
  * one signal, "User frustration", with its version 1
  * one cluster for that signal, which the placeholder clusterer links events to

Settings (env or .env): DATABASE_URL, LMNR_PROJECT_API_KEY (only its last 4 characters
are stored, as the hint), LMNR_API_URL (default Laminar Cloud), LMNR_UI_URL,
LMNR_PROJECT_ID (optional, for "Open in Laminar" links).

TODO (encrypted keys): the key column gets a placeholder, never the real key in plain
text. Next step: encrypt LMNR_PROJECT_API_KEY with TRIPWIRE_SECRET_KEY and store that.
"""

import json
import logging
import os

import psycopg
from dotenv import load_dotenv

from .placeholder import FIXED_EMBEDDING, vector_text

log = logging.getLogger("seed")

PROJECT_NAME = "default"
KEY_PLACEHOLDER = b"not-encrypted-yet"

SIGNAL_NAME = "User frustration"
INSTRUCTION = ("Flag the trace if the user shows frustration with the assistant: repeating "
               "themselves, correcting it, complaining, or giving up.")
OUTPUT_SCHEMA = {"type": "object", "properties": {"reason": {"type": "string"}}}


def seed(conn, *, api_url, ui_url, key_hint, laminar_project_id=None):
    """Insert the seed rows unless the project already exists. Returns True if it seeded."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT 1 FROM projects WHERE name = %s", (PROJECT_NAME,))
        if cur.fetchone():
            return False
        cur.execute(
            """INSERT INTO projects (name, laminar_api_url, laminar_ui_url, laminar_project_id,
                                     laminar_api_key_encrypted, laminar_api_key_hint)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (PROJECT_NAME, api_url, ui_url, laminar_project_id or None, KEY_PLACEHOLDER, key_hint),
        )
        project_id = cur.fetchone()[0]
        cur.execute("INSERT INTO signals (project_id, name) VALUES (%s, %s) RETURNING id",
                    (project_id, SIGNAL_NAME))
        signal_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
               VALUES (%s, 1, %s, %s, 'placeholder')""",
            (signal_id, INSTRUCTION, json.dumps(OUTPUT_SCHEMA)),
        )
        cur.execute(
            """INSERT INTO clusters (signal_id, name, description, centroid)
               VALUES (%s, 'Placeholder cluster', 'Seeded for the skeleton', %s)""",
            (signal_id, vector_text(FIXED_EMBEDDING)),
        )
    return True


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    key = os.environ.get("LMNR_PROJECT_API_KEY", "")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        done = seed(conn,
                    api_url=os.environ.get("LMNR_API_URL", "https://api.lmnr.ai"),
                    ui_url=os.environ.get("LMNR_UI_URL", "https://laminar.sh"),
                    key_hint=key[-4:] if key else "none",
                    laminar_project_id=os.environ.get("LMNR_PROJECT_ID"))
    log.info("seeded project, signal and cluster" if done else "already seeded; nothing to do")


if __name__ == "__main__":
    main()
