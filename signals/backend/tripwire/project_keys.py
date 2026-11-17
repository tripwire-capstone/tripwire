"""The API keys a project uses: the one saved on the Settings page, else the one in the environment.

    key = project_key(conn, project_id, "openai")
    key.value    # the key to use, or None when there's none anywhere
    key.source   # "saved" | "environment" | "missing"

Saved keys live encrypted on `projects` (tripwire/crypto.py). The environment fallback
(LMNR_PROJECT_API_KEY / OPENAI_API_KEY: .env locally, SSM on AWS) keeps local development and
deploys that predate the Settings page working. Clearing a saved key brings the fallback back.

A saved key that can't be decrypted (TRIPWIRE_SECRET_KEY changed or missing) is never an error
here: it counts as not saved, so the environment key is used, and `unreadable` is True so the
Settings page can say "set it again".

Workers look keys up on every loop, so a key saved on the Settings page is used without a restart.
They report what they found through a KeyWatch, which logs only when it changes (missing, back,
replaced): one line, not one per loop. A restart logs the current state once more.
"""

import os
from dataclasses import dataclass

from psycopg import sql

from . import crypto


# kind -> (encrypted column, hint column, environment variable)
KINDS = {
    "laminar": ("laminar_api_key_encrypted", "laminar_api_key_hint", "LMNR_PROJECT_API_KEY"),
    "openai": ("openai_api_key_encrypted", "openai_api_key_hint", "OPENAI_API_KEY"),
}


@dataclass(frozen=True)
class ProjectKey:
    value: str | None = None     # the key to use; never logged or returned by the API
    source: str = "missing"      # "saved", "environment" or "missing"
    hint: str | None = None      # "TGmC...vvzt", safe to show
    unreadable: bool = False     # a saved key exists but doesn't decrypt


def hint_for(key):
    """First 4 and last 4 characters, as Laminar shows its keys. Short keys show only the end."""
    return f"{key[:4]}...{key[-4:]}" if len(key) >= 16 else f"...{key[-4:]}"


def project_key(conn, project_id, kind, environ=os.environ):
    encrypted_column, hint_column, _ = KINDS[kind]
    row = conn.execute(
        sql.SQL("SELECT {}, {} FROM projects WHERE id = %s").format(
            sql.Identifier(encrypted_column), sql.Identifier(hint_column)),
        (project_id,)).fetchone()
    stored, stored_hint = row if row else (None, None)
    return resolve(kind, stored, stored_hint, environ)


def resolve(kind, stored, stored_hint, environ=os.environ):
    """The key to use from a project's stored value and the environment."""
    unreadable = False
    if stored is not None:
        try:
            return ProjectKey(crypto.decrypt(stored, environ), "saved", stored_hint)
        except (crypto.SecretKeyError, crypto.UnreadableError):
            unreadable = True   # KeyWatch logs it once
    from_environment = environ.get(KINDS[kind][2], "").strip()
    if from_environment:
        return ProjectKey(from_environment, "environment", hint_for(from_environment), unreadable)
    return ProjectKey(unreadable=unreadable)


def save_key(conn, project_id, kind, key, environ=os.environ):
    """Encrypt and store `key`. Raises crypto.SecretKeyError when there's no master key."""
    encrypted_column, hint_column, _ = KINDS[kind]
    conn.execute(
        sql.SQL("UPDATE projects SET {} = %s, {} = %s WHERE id = %s").format(
            sql.Identifier(encrypted_column), sql.Identifier(hint_column)),
        (crypto.encrypt(key, environ), hint_for(key), project_id))


def clear_key(conn, project_id, kind):
    """Forget the saved key; the environment's key (if any) takes over."""
    encrypted_column, hint_column, _ = KINDS[kind]
    conn.execute(
        sql.SQL("UPDATE projects SET {} = NULL, {} = NULL WHERE id = %s").format(
            sql.Identifier(encrypted_column), sql.Identifier(hint_column)),
        (project_id,))


def single_project_key(conn, kind, environ=os.environ):
    """For a worker that isn't tied to one project (the clusterer): the project's key. There's one
    project for now; with several, the clusterer would need each event's project key instead."""
    row = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return resolve(kind, None, None, environ)
    return project_key(conn, row[0], kind, environ)


LABELS = {"laminar": "Laminar", "openai": "OpenAI"}


class KeyWatch:
    """Logs a worker's keys only when they change, so a missing key is one line, not one per loop.

        watch = KeyWatch(log)
        key = watch.check(f"project {name}", "laminar", project_key(conn, project_id, "laminar"))

    Returns the key unchanged. Thread-safe enough for the runner: a duplicate line at worst.
    """

    def __init__(self, logger):
        self._log = logger
        self._seen = {}

    def check(self, where, kind, key):
        state = (key.source, key.hint, key.unreadable)
        if self._seen.get((where, kind)) != state:
            self._seen[(where, kind)] = state
            label = LABELS[kind]
            if key.unreadable:
                self._log.warning("%s: the saved %s key can't be read (TRIPWIRE_SECRET_KEY changed or "
                                  "missing): set it again in Settings", where, label)
            if key.value is None:
                self._log.warning("%s: no %s key: add it in Settings", where, label)
            else:
                origin = "saved in Settings" if key.source == "saved" else "from the environment"
                self._log.info("%s: using the %s key %s (%s)", where, label, origin, key.hint)
        return key
