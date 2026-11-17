"""Skip similar events: only the first critical event in a cluster sends a Slack alert.

The notifier waits up to CLUSTER_WAIT_SECONDS for the clusterer to place a critical event, then
sends it, skips it as a repeat, or (not placed yet) looks again on the next poll.
"""

from tripwire.api.slack_settings import get_slack_status, save_slack_webhook
from tripwire.placeholder import vector_text
from tripwire.slack import SlackResult
from tripwire.slack_demo import EMBEDDING_SIZE, add_critical_run
from tripwire.slack_setup import describe
from tests.test_notifier import make_due, poll, slack_out  # noqa: F401  (slack_out is a fixture)
from tests.test_slack_demo import ENV, WEBHOOK, project  # noqa: F401  (project is a fixture)

WAIT = 60  # the notifier's real wait, for the tests that check waiting


def critical_run(conn, project):
    """A new finished run with one critical finding. Returns (run_id, event_id)."""
    add_critical_run(conn, project)
    return conn.execute("SELECT run_id, id FROM signal_events ORDER BY id DESC LIMIT 1").fetchone()


def new_cluster(conn):
    (signal_id,) = conn.execute("SELECT id FROM signals").fetchone()
    (cluster_id,) = conn.execute(
        "INSERT INTO clusters (signal_id, name) VALUES (%s, 'Refunds sent to gift cards') RETURNING id",
        (signal_id,)).fetchone()
    return cluster_id


def place(conn, event_id, cluster_id):
    """What the clusterer does: embed the event and put it in a cluster."""
    conn.execute("INSERT INTO event_embeddings (event_id, model, embedding) VALUES (%s, 'test', %s::vector)",
                 (event_id, vector_text([0.0] * EMBEDDING_SIZE)))
    conn.execute("INSERT INTO cluster_members (event_id, cluster_id) VALUES (%s, %s)", (event_id, cluster_id))


def status_of(conn, run_id):
    return conn.execute("""SELECT status, cluster_id FROM slack_notification_attempts
                           WHERE notification_type = 'critical_run' AND record_id = %s""",
                        (run_id,)).fetchone()


def critical_sends(sent):
    return [text for text in sent if text.startswith(":rotating_light:")]


def connect(conn, project):
    save_slack_webhook(conn, project, WEBHOOK, ENV)


def test_a_repeat_in_a_cluster_that_already_alerted_is_skipped(conn, project, slack_out):
    sent, _ = slack_out
    connect(conn, project)
    cluster = new_cluster(conn)
    first_run, first_event = critical_run(conn, project)
    place(conn, first_event, cluster)

    poll(conn, cluster_wait=WAIT)
    assert len(critical_sends(sent)) == 1 and status_of(conn, first_run) == ("sent", None)

    repeat_run, repeat_event = critical_run(conn, project)
    place(conn, repeat_event, cluster)
    poll(conn, cluster_wait=WAIT)

    assert len(critical_sends(sent)) == 1
    assert status_of(conn, repeat_run) == ("skipped", cluster)
    status = get_slack_status(conn, project)
    assert (status["sent"], status["skipped"]) == (2, 1)  # sent: the first critical + the new cluster
    assert "1 skipped as repeats" in describe(status)


def test_two_repeats_found_in_the_same_poll_send_only_the_first(conn, project, slack_out):
    sent, _ = slack_out
    connect(conn, project)
    cluster = new_cluster(conn)
    first_run, first_event = critical_run(conn, project)
    second_run, second_event = critical_run(conn, project)
    place(conn, first_event, cluster)
    place(conn, second_event, cluster)

    poll(conn, cluster_wait=WAIT)

    assert len(critical_sends(sent)) == 1
    assert status_of(conn, first_run)[0] == "sent"
    assert status_of(conn, second_run) == ("skipped", cluster)


def test_the_first_critical_event_in_each_cluster_is_sent(conn, project, slack_out):
    sent, _ = slack_out
    connect(conn, project)
    for _ in range(2):
        _, event = critical_run(conn, project)
        place(conn, event, new_cluster(conn))

    poll(conn, cluster_wait=WAIT)

    assert len(critical_sends(sent)) == 2


def test_an_unclustered_event_waits_for_the_clusterer_then_is_sent(conn, project, slack_out):
    sent, _ = slack_out
    connect(conn, project)
    run, _ = critical_run(conn, project)

    poll(conn, cluster_wait=WAIT)
    assert sent == [] and status_of(conn, run) is None  # not decided: looked at again next poll

    # A minute later the clusterer still hasn't placed it (stuck, or no OpenAI key): send anyway.
    conn.execute("UPDATE projects SET slack_enabled_at = slack_enabled_at - interval '2 minutes'")
    conn.execute("UPDATE signal_runs SET finished_at = finished_at - interval '61 seconds' WHERE id = %s",
                 (run,))
    poll(conn, cluster_wait=WAIT)
    assert len(critical_sends(sent)) == 1


def test_a_failed_summary_is_sent_without_waiting(conn, project, slack_out):
    sent, _ = slack_out
    connect(conn, project)
    _, event = critical_run(conn, project)
    conn.execute("""INSERT INTO event_summaries (event_id, status, model, prompt_version, attempts,
                                                 error_kind, last_error, finished_at)
                    VALUES (%s, 'failed', 'test', 1, 1, 'refused', 'refused', now())""", (event,))

    poll(conn, cluster_wait=WAIT)

    assert len(critical_sends(sent)) == 1


def test_an_earlier_alert_that_failed_does_not_silence_the_cluster(conn, project, slack_out):
    sent, results = slack_out
    results.append(SlackResult(ok=False, error="HTTP 404: no_service"))  # the first alert never arrives
    connect(conn, project)
    cluster = new_cluster(conn)
    _, first_event = critical_run(conn, project)
    place(conn, first_event, cluster)
    poll(conn, cluster_wait=WAIT)

    repeat_run, repeat_event = critical_run(conn, project)
    place(conn, repeat_event, cluster)
    poll(conn, cluster_wait=WAIT)

    assert status_of(conn, repeat_run)[0] == "sent"


def test_alerts_from_before_reconnecting_do_not_count(conn, project, slack_out):
    sent, _ = slack_out
    connect(conn, project)
    cluster = new_cluster(conn)
    _, first_event = critical_run(conn, project)
    place(conn, first_event, cluster)
    poll(conn, cluster_wait=WAIT)

    connect(conn, project)  # e.g. a new channel: it hasn't seen this problem
    repeat_run, repeat_event = critical_run(conn, project)
    place(conn, repeat_event, cluster)
    poll(conn, cluster_wait=WAIT)

    assert status_of(conn, repeat_run)[0] == "sent"


def test_a_retry_is_not_skipped_after_its_event_joins_an_alerted_cluster(conn, project, slack_out):
    sent, results = slack_out
    connect(conn, project)
    cluster = new_cluster(conn)
    _, first_event = critical_run(conn, project)
    place(conn, first_event, cluster)
    poll(conn, cluster_wait=WAIT)

    # Decided to send without waiting, but the send failed and will be retried.
    results.append(SlackResult(ok=False, retryable=True, error="TimeoutError"))
    retry_run, retry_event = critical_run(conn, project)
    poll(conn)
    place(conn, retry_event, cluster)
    make_due(conn)
    poll(conn, cluster_wait=WAIT)

    assert status_of(conn, retry_run)[0] == "sent"
    assert len(critical_sends(sent)) == 3  # the first alert, the failed try, the retry
