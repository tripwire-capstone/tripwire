"""Tests for tripwire/project_keys.py: a saved key wins, the environment's is the fallback."""

import pytest

from tripwire import crypto
from tripwire.project_keys import clear_key, hint_for, project_key, resolve, save_key

SECRET = "ab" * 32
ENV = {"TRIPWIRE_SECRET_KEY": SECRET, "LMNR_PROJECT_API_KEY": "env-laminar-key-0000",
       "OPENAI_API_KEY": "sk-env-openai-key-1111"}


def stored(key, secret=SECRET):
    return crypto.encrypt(key, {"TRIPWIRE_SECRET_KEY": secret})


# ------------------------------------------------------------------ hint_for

def test_hint_for_shows_first_and_last_four_like_laminar():
    assert hint_for("TGmCabcdefghijklvvzt") == "TGmC...vvzt"


def test_hint_for_a_short_key_shows_only_the_end():
    assert hint_for("abcdefgh") == "...efgh"


# ------------------------------------------------------------------ resolve (no database)

def test_resolve_prefers_the_saved_key():
    key = resolve("openai", stored("sk-saved-key-2222222"), "sk-s...2222", ENV)
    assert (key.value, key.source, key.hint, key.unreadable) == \
        ("sk-saved-key-2222222", "saved", "sk-s...2222", False)


def test_resolve_falls_back_to_the_environment():
    key = resolve("laminar", None, None, ENV)
    assert (key.value, key.source, key.hint) == ("env-laminar-key-0000", "environment", "env-...0000")


def test_resolve_with_no_key_anywhere_is_missing():
    key = resolve("openai", None, None, {"OPENAI_API_KEY": "  "})
    assert (key.value, key.source, key.hint, key.unreadable) == (None, "missing", None, False)


def test_resolve_ignores_an_unreadable_saved_key_and_uses_the_environment():
    key = resolve("openai", stored("sk-saved-key-2222222", secret="cd" * 32), "sk-s...2222", ENV)
    assert (key.value, key.source, key.unreadable) == ("sk-env-openai-key-1111", "environment", True)


def test_resolve_without_a_secret_key_treats_the_saved_key_as_unreadable():
    key = resolve("laminar", stored("lmnr-saved-key-3333"), "lmnr...3333", {})
    assert (key.value, key.source, key.unreadable) == (None, "missing", True)


# ------------------------------------------------------------------ with Postgres

def project(conn):
    return conn.execute("SELECT id FROM projects").fetchone()[0]


def test_project_key_saves_then_clears_back_to_the_environment(conn):
    project_id = project(conn)
    save_key(conn, project_id, "laminar", "lmnr-saved-key-3333", ENV)

    saved = project_key(conn, project_id, "laminar", ENV)
    assert (saved.value, saved.source, saved.hint) == ("lmnr-saved-key-3333", "saved", "lmnr...3333")

    clear_key(conn, project_id, "laminar")
    assert project_key(conn, project_id, "laminar", ENV).source == "environment"


def test_project_key_stores_only_ciphertext_and_the_hint(conn):
    project_id = project(conn)
    save_key(conn, project_id, "openai", "sk-saved-key-2222222", ENV)
    encrypted, hint = conn.execute("SELECT openai_api_key_encrypted, openai_api_key_hint FROM projects").fetchone()
    assert b"sk-saved-key-2222222" not in bytes(encrypted) and hint == "sk-s...2222"


def test_project_key_kinds_are_saved_separately(conn):
    project_id = project(conn)
    save_key(conn, project_id, "openai", "sk-saved-key-2222222", ENV)
    assert project_key(conn, project_id, "laminar", ENV).source == "environment"


def test_save_key_without_a_secret_key_saves_nothing(conn):
    project_id = project(conn)
    with pytest.raises(crypto.SecretKeyError):
        save_key(conn, project_id, "openai", "sk-saved-key-2222222", {})
    assert conn.execute("SELECT openai_api_key_encrypted FROM projects").fetchone()[0] is None


def test_projects_table_refuses_a_key_without_its_hint(conn):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE projects SET openai_api_key_encrypted = 'x'")
