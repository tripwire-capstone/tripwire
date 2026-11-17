"""Slack setup + demo rows end to end: connect, add demo rows, the notifier sends each once."""

import json
from threading import Event

import pytest

from tripwire import crypto, notifier
from tripwire.api.slack_settings import disconnect_slack, get_slack_status, save_slack_webhook
from tripwire.slack import SlackResult
from tripwire.slack_demo import add_cluster, add_critical_run
from tripwire.slack_setup import find_project

WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXX"
ENV = {"TRIPWIRE_SECRET_KEY": "ab" * 32}  # the master key, as tripwire/crypto.py expects it
SCHEMA = [
    {"name": "reason", "type": "string", "description": "Why the user seems frustrated"},
    {"name": "severity", "type": "enum", "choices": ["low", "medium", "high"]},
]


@pytest.fixture
def project(conn):
    project_id = find_project(conn, "test")
    (signal_id,) = conn.execute(
        "INSERT INTO signals (project_id, name) VALUES (%s, 'Frustration') RETURNING id",
        (project_id,),
    ).fetchone()
    conn.execute(
        """INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
           VALUES (%s, 1, 'Flag frustration', %s, 'gpt-6-luna')""",
        (signal_id, json.dumps(SCHEMA)),
    )
    return project_id


@pytest.fixture
def sent(monkeypatch):
    messages = []

    def fake_post(text, *, webhook_url):
        messages.append((text, webhook_url))
        return SlackResult(ok=True)

    monkeypatch.setattr(notifier, "post_slack", fake_post)
    return messages


def test_demo_rows_are_sent_once_after_connecting(conn, project, sent):
    add_critical_run(conn, project)  # before connecting: never sent

    save_slack_webhook(conn, project, WEBHOOK, ENV)
    add_critical_run(conn, project)
    add_cluster(conn, project)

    notifier.poll_once(conn, Event(), environ=ENV, cluster_wait=None)
    notifier.poll_once(conn, Event(), environ=ENV, cluster_wait=None)

    assert [url for _, url in sent] == [WEBHOOK, WEBHOOK]
    assert "*Critical*" in sent[0][0] and "credit card" in sent[0][0]
    assert "*New cluster*" in sent[1][0] and "The user repeats their email" in sent[1][0]
    assert "3 findings in 2 conversations" in sent[1][0]
    assert get_slack_status(conn, project)["verified_at"] is not None


def test_demo_rows_have_the_real_pipelines_shape(conn, project):
    add_critical_run(conn, project)
    add_cluster(conn, project)

    events = conn.execute(
        """SELECT e.severity, e.payload, e.span_ids, e.evidence_labels, e.judge_input,
                  r.analysis, r.raw_output, t.session_id
           FROM signal_events e JOIN signal_runs r ON r.id = e.run_id JOIN traces t ON t.id = e.trace_id
           ORDER BY e.id"""
    ).fetchall()
    assert [e[0] for e in events] == ["critical", "warning", "warning", "warning"]
    for severity, payload, span_ids, labels, judge_input, analysis, raw_output, _ in events:
        assert set(payload) == {"reason", "severity"}
        assert payload["severity"] == ("high" if severity == "critical" else "medium")
        assert len(span_ids) == len(labels) and labels
        assert all(f"[{label}]" in judge_input for label in labels)
        assert "<current_turn>" in judge_input and "<earlier_turns" in judge_input
        assert json.loads(raw_output)["findings"][0]["fields"] == payload
        assert analysis
    assert len({e[7] for e in events[1:]}) == 2  # the cluster spans two sessions

    (name, summaries) = conn.execute(
        """SELECT c.name, count(s.event_id) FILTER (WHERE s.status = 'done')
           FROM clusters c JOIN cluster_members m ON m.cluster_id = c.id
           JOIN event_summaries s ON s.event_id = m.event_id GROUP BY c.name"""
    ).fetchone()
    assert summaries == 3
    assert name.startswith("The user repeats their email")

    add_cluster(conn, project)  # a second demo cluster gets a numbered name, like the clusterer's
    assert conn.execute("SELECT max(name) FROM clusters").fetchone()[0].endswith("(2)")


def test_nothing_is_sent_after_disconnecting(conn, project, sent):
    save_slack_webhook(conn, project, WEBHOOK, ENV)
    disconnect_slack(conn, project)
    add_critical_run(conn, project)
    add_cluster(conn, project)

    notifier.poll_once(conn, Event(), environ=ENV, cluster_wait=None)

    assert sent == []


def test_connect_rejects_non_slack_urls(conn, project):
    with pytest.raises(ValueError):
        save_slack_webhook(conn, project, "https://example.com/hook", ENV)


def test_connect_needs_a_valid_master_key(conn, project):
    with pytest.raises(crypto.SecretKeyError):
        save_slack_webhook(conn, project, WEBHOOK, {"TRIPWIRE_SECRET_KEY": "not-64-hex"})
    assert get_slack_status(conn, project)["configured"] is False


def test_the_webhook_is_stored_encrypted_with_the_shared_master_key(conn, project):
    save_slack_webhook(conn, project, WEBHOOK, ENV)
    (stored,) = conn.execute("SELECT slack_webhook_url_encrypted FROM projects WHERE id = %s",
                             (project,)).fetchone()
    assert WEBHOOK.encode() not in bytes(stored)
    assert crypto.decrypt(stored, ENV) == WEBHOOK
