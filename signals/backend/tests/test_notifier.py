"""Slack alerts: what the messages say, and how failed sends are retried or given up."""

import uuid
from threading import Event

import pytest
from cryptography.fernet import Fernet

from tripwire import notifier, slack
from tripwire.api.slack_settings import get_slack_status, save_slack_webhook
from tripwire.slack import SlackResult, post_slack
from tripwire.slack_demo import add_cluster, add_critical_run
from tests.test_slack_demo import WEBHOOK, project  # noqa: F401  (project is a fixture)

UI = "https://tripwire.test"


@pytest.fixture
def cipher():
    return Fernet(Fernet.generate_key())


@pytest.fixture
def slack_out(monkeypatch):
    """Fake Slack: answers with the queued results in order (ok once they run out); records texts."""
    sent, results = [], []

    def fake_post(text, *, webhook_url):
        sent.append(text)
        return results.pop(0) if results else SlackResult(ok=True)

    monkeypatch.setattr(notifier, "post_slack", fake_post)
    return sent, results


def poll(conn, cipher):
    notifier.poll_once(conn, cipher, Event(), UI)


def attempt(conn, kind="critical_run"):
    return conn.execute(
        """SELECT status, tries, next_try_at > now() + interval '1 second', last_error,
                  extract(epoch FROM next_try_at - now())
           FROM slack_notification_attempts WHERE notification_type = %s""",
        (kind,),
    ).fetchone()


def make_due(conn):
    conn.execute("UPDATE slack_notification_attempts SET next_try_at = now() - interval '1 second' "
                 "WHERE status = 'pending'")


# ------------------------------------------------------------------ messages


def test_critical_alert_shows_the_finding_and_links_to_it(conn, project, cipher, slack_out):
    sent, _ = slack_out
    laminar_project = uuid.uuid4()
    conn.execute("UPDATE projects SET laminar_project_id = %s, laminar_ui_url = 'https://laminar.test/'",
                 (laminar_project,))
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)
    signal_id, event_id, trace = conn.execute(
        """SELECT v.signal_id, e.id, t.laminar_trace_id FROM signal_events e
           JOIN signal_versions v ON v.id = e.signal_version_id JOIN traces t ON t.id = e.trace_id"""
    ).fetchone()

    poll(conn, cipher)

    (text,) = sent
    assert text.startswith(":rotating_light: *Critical* in *Frustration*")
    assert "\n> The user says for the third time that the refund must go to their credit card." in text
    assert f"<{UI}/signals/{signal_id}?event={event_id}&severity=critical|Open in Tripwire>" in text
    assert f"<https://laminar.test/project/{laminar_project}/traces/{trace}|View trace in Laminar>" in text
    assert "run #" not in text


def test_critical_alert_has_no_laminar_link_without_a_laminar_project(conn, project, cipher, slack_out):
    sent, _ = slack_out
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)

    poll(conn, cipher)

    assert "Open in Tripwire" in sent[0] and "Laminar" not in sent[0]


def test_cluster_alert_shows_its_size_and_links_to_it(conn, project, cipher, slack_out):
    sent, _ = slack_out
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_cluster(conn, project)
    signal_id, cluster_id = conn.execute("SELECT signal_id, id FROM clusters").fetchone()

    poll(conn, cipher)

    (text,) = sent
    assert text.startswith(":jigsaw: *New cluster* in *Frustration* · 3 findings in 2 conversations")
    assert "\n> The user repeats their email" in text
    assert text.endswith(f"<{UI}/signals/{signal_id}?cluster={cluster_id}|Open cluster in Tripwire>")


def test_finding_text_prefers_the_signals_first_text_field():
    schema = [{"name": "level", "type": "enum"}, {"name": "what", "type": "string"},
              {"name": "why", "type": "string"}]
    assert notifier.finding_text({"level": "high", "what": "", "why": "Because"}, schema, None) == "Because"
    assert notifier.finding_text({"other": "Loose text"}, schema, None) == "Loose text"
    assert notifier.finding_text({}, schema, "Old description") == "Old description"


def test_long_findings_are_shortened_at_a_word_and_markup_is_escaped(conn, project, cipher, slack_out):
    assert notifier.shorten("word " * 100, 20) == "word word word word…"
    assert notifier.shorten("a\n  b") == "a b"
    sent, _ = slack_out
    conn.execute("UPDATE signals SET name = 'Refunds <&> returns'")
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)

    poll(conn, cipher)

    assert "*Refunds &lt;&amp;&gt; returns*" in sent[0]


# ------------------------------------------------------------------ retries


def test_a_failed_send_is_retried_later_and_then_sent(conn, project, cipher, slack_out):
    sent, results = slack_out
    results.append(SlackResult(ok=False, retryable=True, error="TimeoutError"))
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)

    poll(conn, cipher)
    status, tries, later, error, wait = attempt(conn)
    assert (status, tries, later, error) == ("pending", 1, True, "TimeoutError")
    assert 25 < wait <= 30

    poll(conn, cipher)  # not due yet
    assert len(sent) == 1

    make_due(conn)
    poll(conn, cipher)
    assert len(sent) == 2
    assert attempt(conn)[:2] == ("sent", 2)
    assert get_slack_status(conn, project)["verified_at"] is not None


def test_slacks_retry_after_is_respected(conn, project, cipher, slack_out):
    _, results = slack_out
    results.append(SlackResult(ok=False, retryable=True, error="HTTP 429", retry_after=900))
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)

    poll(conn, cipher)

    assert 890 < attempt(conn)[4] <= 900


def test_retries_stop_after_the_last_try(conn, project, cipher, slack_out):
    sent, results = slack_out
    results.extend([SlackResult(ok=False, retryable=True, error="HTTP 503")] * 10)
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)

    for _ in range(notifier.MAX_TRIES + 2):
        poll(conn, cipher)
        make_due(conn)

    assert len(sent) == notifier.MAX_TRIES
    assert attempt(conn)[:2] == ("failed", notifier.MAX_TRIES)
    status = get_slack_status(conn, project)
    assert (status["sent"], status["retrying"], status["failed"]) == (0, 0, 1)
    assert status["last_error"] == "HTTP 503"


def test_a_refused_message_fails_at_once(conn, project, cipher, slack_out):
    sent, results = slack_out
    results.append(SlackResult(ok=False, error="HTTP 404: no_service"))
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)

    poll(conn, cipher)
    make_due(conn)
    poll(conn, cipher)

    assert len(sent) == 1
    assert attempt(conn)[:2] == ("failed", 1)
    assert get_slack_status(conn, project)["verified_at"] is None


def test_an_undecryptable_webhook_fails_and_says_why(conn, project, cipher, slack_out):
    sent, _ = slack_out
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)

    poll(conn, Fernet(Fernet.generate_key()))  # the key changed

    assert sent == []
    status, _, _, error, _ = attempt(conn)
    assert status == "failed" and "TRIPWIRE_SECRET_KEY" in error


def test_a_claim_left_by_a_dead_notifier_is_retried_after_it_expires(conn, project, cipher, slack_out):
    sent, _ = slack_out
    save_slack_webhook(conn, project, WEBHOOK, cipher)
    add_critical_run(conn, project)
    run_id, webhook = conn.execute(
        "SELECT r.id, p.slack_webhook_url_encrypted FROM signal_runs r, projects p").fetchone()
    assert notifier.claim(conn, "critical_run", run_id, project_id=project, encrypted_webhook=webhook) == 1

    poll(conn, cipher)  # still claimed
    assert sent == []

    make_due(conn)
    poll(conn, cipher)
    assert len(sent) == 1 and attempt(conn)[:2] == ("sent", 2)


# ------------------------------------------------------------------ post_slack


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    def read(self, _n):
        return self.body

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def fake_connection(response=None, error=None):
    class FakeConnection:
        def __init__(self, host, timeout):
            assert host == "hooks.slack.com"

        def request(self, *_args, **_kwargs):
            if error:
                raise error

        def getresponse(self):
            return response

        def close(self):
            pass

    return FakeConnection


@pytest.mark.parametrize("response, error, expected", [
    (FakeResponse(200, b"ok"), None, SlackResult(ok=True)),
    (FakeResponse(404, b"no_service"), None, SlackResult(ok=False, error="HTTP 404: no_service")),
    (FakeResponse(410, b"channel_is_archived"), None,
     SlackResult(ok=False, error="HTTP 410: channel_is_archived")),
    (FakeResponse(500), None, SlackResult(ok=False, retryable=True, error="HTTP 500")),
    (FakeResponse(429, b"rate_limited", {"Retry-After": "12"}), None,
     SlackResult(ok=False, retryable=True, error="HTTP 429: rate_limited", retry_after=12.0)),
    (None, TimeoutError(), SlackResult(ok=False, retryable=True, error="TimeoutError")),
])
def test_post_slack_says_which_failures_are_worth_retrying(monkeypatch, response, error, expected):
    monkeypatch.setattr(slack, "HTTPSConnection", fake_connection(response, error))
    assert post_slack("hi", webhook_url=WEBHOOK) == expected


def test_post_slack_rejects_a_non_slack_url_without_sending(monkeypatch):
    monkeypatch.setattr(slack, "HTTPSConnection", None)  # would fail if called
    assert post_slack("hi", webhook_url="https://example.com/hook") == SlackResult(
        ok=False, error="invalid webhook URL")
