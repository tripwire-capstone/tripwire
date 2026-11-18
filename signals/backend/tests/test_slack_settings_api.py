"""The Settings page's Slack endpoints (tripwire/api/slack_settings.py) and the catch-up note."""

from threading import Event

import pytest
from fastapi.testclient import TestClient

from tripwire import notifier
from tripwire.api.app import app
from tripwire.api.db import get_conn
from tripwire.api.slack_settings import get_slack_post, get_ui_url
from tripwire.slack import SlackResult
from tripwire.slack_demo import add_critical_run
from tests.test_notifier import make_due
from tests.test_slack_demo import ENV, WEBHOOK, project  # noqa: F401  (project is a fixture)

OTHER = "https://hooks.slack.com/services/T000/B111/YYYY"
UI = "https://tripwire.test"


class FakeSlack:
    """Answers with the queued results in order (ok once they run out); records (text, url)."""

    def __init__(self):
        self.sent, self.results = [], []

    def __call__(self, text, *, webhook_url):
        self.sent.append((text, webhook_url))
        return self.results.pop(0) if self.results else SlackResult(ok=True)


@pytest.fixture
def slack(monkeypatch):
    fake = FakeSlack()
    monkeypatch.setattr(notifier, "post_slack", fake)  # the notifier's alerts go to the same fake
    return fake


@pytest.fixture
def client(conn, project, slack, monkeypatch):
    monkeypatch.setenv("TRIPWIRE_SECRET_KEY", ENV["TRIPWIRE_SECRET_KEY"])
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_slack_post] = lambda: slack
    app.dependency_overrides[get_ui_url] = lambda: UI
    yield TestClient(app)
    app.dependency_overrides.clear()


def connect(client, url=WEBHOOK):
    return client.put("/api/settings/slack", json={"webhook_url": url})


# ------------------------------------------------------------------ reading

def test_not_connected_at_first(client):
    body = client.get("/api/settings/slack").json()
    assert body["configured"] is False and body["hint"] is None and body["sent"] == 0


def test_status_shows_a_hint_never_the_webhook(client):
    connect(client)
    response = client.get("/api/settings/slack")
    assert response.json()["hint"] == "hooks.slack.com/…XXXX"
    assert "B000" not in response.text


def test_a_webhook_saved_under_another_secret_key_is_unreadable(client, monkeypatch):
    connect(client)
    monkeypatch.setenv("TRIPWIRE_SECRET_KEY", "cd" * 32)
    body = client.get("/api/settings/slack").json()
    assert body["configured"] is True and body["unreadable"] is True and body["hint"] is None


# ------------------------------------------------------------------ connecting

def test_connecting_posts_a_connect_message_and_saves_the_webhook(client, slack):
    response = connect(client)

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True and body["verified_at"] is not None
    ((text, url),) = slack.sent
    assert url == WEBHOOK and "Tripwire is connected to this channel" in text
    assert "Before connecting" not in text  # nothing found in the last 24 hours


def test_the_connect_message_mentions_recent_critical_events(client, conn, project, slack):
    add_critical_run(conn, project)
    add_critical_run(conn, project)
    (signal_id,) = conn.execute("SELECT id FROM signals").fetchone()

    connect(client)

    (text, _), = slack.sent
    assert "found 2 critical events in the last 24 hours" in text
    assert f"<{UI}/signals/{signal_id}?severity=critical&pastHours=24|Frustration>: 2" in text


def test_a_webhook_slack_refuses_is_not_saved(client, slack):
    slack.results.append(SlackResult(ok=False, error="HTTP 404: no_service"))
    response = connect(client)

    assert response.status_code == 422
    assert "removed or revoked" in response.json()["detail"]
    assert client.get("/api/settings/slack").json()["configured"] is False


def test_slack_unreachable_saves_nothing(client, slack):
    slack.results.append(SlackResult(ok=False, retryable=True, error="TimeoutError"))
    response = connect(client)

    assert response.status_code == 502 and "Try again" in response.json()["detail"]
    assert client.get("/api/settings/slack").json()["configured"] is False


def test_a_refused_new_webhook_keeps_the_working_one(client, slack):
    connect(client)
    slack.results.append(SlackResult(ok=False, error="HTTP 404: channel_is_archived"))

    assert connect(client, OTHER).status_code == 422
    assert client.get("/api/settings/slack").json()["hint"] == "hooks.slack.com/…XXXX"
    assert [url for _, url in slack.sent] == [WEBHOOK, OTHER]  # no goodbye: nothing moved


def test_not_a_slack_url_is_rejected_before_posting(client, slack):
    response = connect(client, "https://example.com/hook")
    assert response.status_code == 422 and slack.sent == []


def test_an_unusable_secret_key_stops_before_posting(client, slack, monkeypatch):
    monkeypatch.setenv("TRIPWIRE_SECRET_KEY", "short")
    response = connect(client)
    assert response.status_code == 503 and slack.sent == []


def test_replacing_the_webhook_says_goodbye_to_the_old_channel(client, slack):
    connect(client)
    connect(client, OTHER)

    assert [url for _, url in slack.sent] == [WEBHOOK, OTHER, WEBHOOK]
    assert "moved to another channel" in slack.sent[2][0]
    assert client.get("/api/settings/slack").json()["hint"] == "hooks.slack.com/…YYYY"


def test_connecting_the_same_webhook_again_says_no_goodbye(client, slack):
    connect(client)
    connect(client)
    assert all("Tripwire is connected" in text for text, _ in slack.sent)


def test_a_failed_goodbye_still_connects_the_new_webhook(client, slack):
    connect(client)
    slack.results.extend([SlackResult(ok=True), SlackResult(ok=False, error="HTTP 404: no_service")])
    assert connect(client, OTHER).json()["hint"] == "hooks.slack.com/…YYYY"


# ------------------------------------------------------------------ disconnecting

def test_disconnecting_says_goodbye_then_forgets_the_webhook(client, slack):
    connect(client)
    body = client.delete("/api/settings/slack").json()

    assert body["configured"] is False and body["hint"] is None
    assert slack.sent[-1] == (slack.sent[-1][0], WEBHOOK) and "turned off" in slack.sent[-1][0]


def test_disconnecting_works_when_the_goodbye_fails(client, slack):
    connect(client)
    slack.results.append(SlackResult(ok=False, error="HTTP 404: no_service"))
    assert client.delete("/api/settings/slack").json()["configured"] is False


def test_disconnecting_when_not_connected_posts_nothing(client, slack):
    assert client.delete("/api/settings/slack").json()["configured"] is False
    assert slack.sent == []


# ------------------------------------------------------------------ catch-up note

def poll(conn):
    notifier.poll_once(conn, Event(), UI, ENV, cluster_wait=None)


def test_after_failed_alerts_the_next_alert_is_followed_by_a_catch_up_note(client, conn, project, slack):
    connect(client)
    slack.results.append(SlackResult(ok=False, error="HTTP 404: no_service"))  # fails for good
    add_critical_run(conn, project)
    poll(conn)

    add_critical_run(conn, project)
    poll(conn)

    texts = [text for text, _ in slack.sent[1:]]  # after the connect message
    assert texts[0].startswith(":rotating_light:") and texts[1].startswith(":rotating_light:")
    assert texts[2].startswith(":inbox_tray: *Catching up:* 1 critical alert couldn't be sent")
    assert f"<{UI}/|Open Tripwire>" in texts[2]

    add_critical_run(conn, project)
    poll(conn)
    assert not slack.sent[-1][0].startswith(":inbox_tray:")  # each missed alert is mentioned once


def test_no_catch_up_note_while_alerts_are_only_retrying(client, conn, project, slack):
    connect(client)
    slack.results.append(SlackResult(ok=False, retryable=True, error="TimeoutError"))
    add_critical_run(conn, project)
    poll(conn)
    make_due(conn)
    poll(conn)  # the retry gets through: nothing was missed

    assert not any(text.startswith(":inbox_tray:") for text, _ in slack.sent)


def test_a_failed_catch_up_note_is_tried_after_the_next_alert(client, conn, project, slack):
    connect(client)
    slack.results.append(SlackResult(ok=False, error="HTTP 404: no_service"))
    add_critical_run(conn, project)
    poll(conn)
    slack.results.extend([SlackResult(ok=True), SlackResult(ok=False, retryable=True, error="TimeoutError")])
    add_critical_run(conn, project)
    poll(conn)  # the alert gets through, its catch-up note doesn't

    add_critical_run(conn, project)
    poll(conn)

    notes = [text for text, _ in slack.sent if text.startswith(":inbox_tray:")]
    assert len(notes) == 2 and "1 critical alert" in notes[1]


def test_failures_from_before_reconnecting_are_not_caught_up(client, conn, project, slack):
    connect(client)
    slack.results.append(SlackResult(ok=False, error="HTTP 404: no_service"))
    add_critical_run(conn, project)
    poll(conn)

    connect(client)  # a fresh start: the new channel never expected those alerts
    add_critical_run(conn, project)
    poll(conn)

    assert not any(text.startswith(":inbox_tray:") for text, _ in slack.sent)
