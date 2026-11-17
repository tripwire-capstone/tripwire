"""Slack setup + demo rows end to end: connect, add demo rows, the notifier sends each once."""

from threading import Event

import pytest
from cryptography.fernet import Fernet

from tripwire import notifier
from tripwire.api.slack_settings import disconnect_slack, get_slack_status, save_slack_webhook
from tripwire.slack_demo import add_cluster, add_critical_run
from tripwire.slack_setup import find_project

WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXX"


@pytest.fixture
def project(conn):
    project_id = find_project(conn, "test")
    (signal_id,) = conn.execute(
        "INSERT INTO signals (project_id, name) VALUES (%s, 'Frustration') RETURNING id",
        (project_id,),
    ).fetchone()
    conn.execute(
        """INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
           VALUES (%s, 1, 'Flag frustration', '[]', 'gpt-6-luna')""",
        (signal_id,),
    )
    return project_id


@pytest.fixture
def sent(monkeypatch):
    messages = []

    def fake_post(text, *, record_type, record_id, webhook_url=None):
        messages.append((record_type, text, webhook_url))
        return True

    monkeypatch.setattr(notifier, "post_slack", fake_post)
    return messages


def test_demo_rows_are_sent_once_after_connecting(conn, project, sent):
    cipher = Fernet(Fernet.generate_key())
    add_critical_run(conn, project)  # before connecting: never sent

    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)
    add_cluster(conn, project)

    notifier.poll_once(conn, cipher, Event())
    notifier.poll_once(conn, cipher, Event())

    assert [(kind, url) for kind, _, url in sent] == [("critical_run", WEBHOOK), ("new_cluster", WEBHOOK)]
    assert "1 critical event" in sent[0][1]
    assert "Demo: users repeating themselves" in sent[1][1]
    assert get_slack_status(conn, project)["verified_at"] is not None


def test_nothing_is_sent_after_disconnecting(conn, project, sent):
    cipher = Fernet(Fernet.generate_key())
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    disconnect_slack(conn, project)
    add_critical_run(conn, project)
    add_cluster(conn, project)

    notifier.poll_once(conn, cipher, Event())

    assert sent == []


def test_connect_rejects_non_slack_urls(conn, project):
    with pytest.raises(ValueError):
        save_slack_webhook(conn, project, "https://example.com/hook", Fernet(Fernet.generate_key()))
