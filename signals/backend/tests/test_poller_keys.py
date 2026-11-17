"""The poller's keys: looked up per project on every poll, and a missing key is logged once."""

import logging
from datetime import timedelta

import pytest

from tripwire.poller.__main__ import poll_all
from tripwire.project_keys import KeyWatch, save_key

SECRET = "ab" * 32


@pytest.fixture
def keys_env(monkeypatch):
    monkeypatch.setenv("TRIPWIRE_SECRET_KEY", SECRET)
    monkeypatch.setenv("LMNR_PROJECT_API_KEY", "env-laminar-key-0000")


def poll_recording(seen):
    def poll(conn, project_id, name, api_url, api_key, lookback, settle):
        seen.append((name, api_key))
    return poll


def poll(conn, watch, seen):
    poll_all(conn, watch, timedelta(hours=24), timedelta(seconds=15), poll=poll_recording(seen))


def watch():
    return KeyWatch(logging.getLogger("poller-test"))


def test_poller_uses_the_environment_key_when_none_is_saved(conn, keys_env):
    seen = []
    poll(conn, watch(), seen)
    assert seen == [("test", "env-laminar-key-0000")]


def test_poller_uses_a_key_saved_in_settings_on_the_next_poll(conn, keys_env):
    seen, same_watch = [], watch()
    poll(conn, same_watch, seen)
    save_key(conn, conn.execute("SELECT id FROM projects").fetchone()[0], "laminar", "lmnr-saved-key-3333")
    poll(conn, same_watch, seen)
    assert seen == [("test", "env-laminar-key-0000"), ("test", "lmnr-saved-key-3333")]


def test_poller_skips_a_project_without_a_key_and_keeps_going(conn, keys_env, monkeypatch):
    monkeypatch.delenv("LMNR_PROJECT_API_KEY")
    conn.execute("""INSERT INTO projects (name, laminar_api_url, laminar_ui_url)
                    VALUES ('second', 'http://x', 'http://x')""")
    save_key(conn, conn.execute("SELECT id FROM projects WHERE name = 'second'").fetchone()[0],
             "laminar", "lmnr-saved-key-3333")
    seen = []
    poll(conn, watch(), seen)
    assert seen == [("second", "lmnr-saved-key-3333")]


def test_poller_logs_a_missing_key_once_not_every_poll(conn, keys_env, monkeypatch, caplog):
    monkeypatch.delenv("LMNR_PROJECT_API_KEY")
    same_watch = watch()
    with caplog.at_level(logging.INFO, logger="poller-test"):
        for _ in range(3):
            poll(conn, same_watch, [])
    assert [record.getMessage() for record in caplog.records] == ["project test: no Laminar key: add it in Settings"]


def test_poller_logs_when_a_missing_key_comes_back(conn, keys_env, monkeypatch, caplog):
    monkeypatch.delenv("LMNR_PROJECT_API_KEY")
    same_watch = watch()
    with caplog.at_level(logging.INFO, logger="poller-test"):
        poll(conn, same_watch, [])
        save_key(conn, conn.execute("SELECT id FROM projects").fetchone()[0], "laminar", "lmnr-saved-key-3333")
        poll(conn, same_watch, [])
        poll(conn, same_watch, [])
    assert [record.getMessage() for record in caplog.records] == [
        "project test: no Laminar key: add it in Settings",
        "project test: using the Laminar key saved in Settings (lmnr...3333)",
    ]
