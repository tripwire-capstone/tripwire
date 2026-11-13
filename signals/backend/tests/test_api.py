import uuid
from datetime import datetime
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from tripwire.api.app import app, get_judge
from tripwire.api.db import get_conn
from tripwire.judge.types import Finding, JudgeError, JudgeResult, Usage

VALID = {
    "name": "Made-up facts",
    "instruction": "Flag answers that state facts no tool returned.",
    "output_schema": [
        {"name": "claim", "type": "string", "description": "The invented fact"},
        {"name": "kind", "type": "enum", "choices": ["number", "place"], "description": "Kind of fact",
         "nullable": True},
    ],
}


@pytest.fixture
def client(conn):
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def create(client, **changes):
    return client.post("/api/signals", json={**VALID, **changes})


def versions(conn):
    return conn.execute("SELECT count(*) FROM signal_versions").fetchone()[0]


# ------------------------------------------------------------------ create


def test_create_makes_a_signal_with_version_1(client):
    r = create(client, template="failure")
    assert r.status_code == 201
    body = r.json()
    assert (body["name"], body["version_number"], body["active"], body["template"]) == \
        ("Made-up facts", 1, True, "failure")
    assert body["output_schema"][0] == {"name": "claim", "type": "string",
                                        "description": "The invented fact", "nullable": False}


@pytest.mark.parametrize("model", [None, "", "  "])
def test_blank_model_defaults_to_the_env_var(client, model):
    response = create(client, model=model)
    assert response.status_code == 201
    assert response.json()["model"] == "gpt-6-luna"


@pytest.mark.parametrize("configured", [None, " ", "stub", "placeholder"])
def test_invalid_default_requires_an_explicit_model(client, conn, monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("JUDGE_DEFAULT_MODEL")
    else:
        monkeypatch.setenv("JUDGE_DEFAULT_MODEL", configured)

    response = create(client)
    assert response.status_code == 503
    assert "JUDGE_DEFAULT_MODEL" in response.json()["detail"]
    assert conn.execute("SELECT count(*) FROM signals").fetchone()[0] == 0
    assert versions(conn) == 0
    assert create(client, model="gpt-5.4-mini").json()["model"] == "gpt-5.4-mini"


def test_invalid_definition_lists_every_problem(client):
    r = create(client, name=" ", output_schema=[{"name": "1bad", "type": "enum", "description": "d"}],
               filters=[{"field": "span_name", "op": "includes", "value": ["x"]}])
    assert r.status_code == 422
    detail = " | ".join(r.json()["detail"])
    for text in ["name must not be blank", "output_schema[0].name", "output_schema[0].choices",
                 "filters[0] is not a supported filter"]:
        assert text in detail


def test_unknown_key_is_rejected(client):
    assert create(client, sampling=0.5).status_code == 422


def test_duplicate_name_is_409(client):
    create(client)
    assert create(client).status_code == 409


def test_no_project_is_503(client, conn):
    conn.execute("DELETE FROM projects")
    assert create(client).status_code == 503


def test_response_never_includes_project_secrets(client):
    assert not any("key" in k or "project" in k for k in create(client).json())


# ------------------------------------------------------------------ read


def test_get_returns_the_current_version(client):
    signal_id = create(client).json()["id"]
    assert client.get(f"/api/signals/{signal_id}").json()["version_number"] == 1


def test_missing_signal_is_404(client):
    assert client.get("/api/signals/999").status_code == 404


# ------------------------------------------------------------------ edit


def test_changed_definition_makes_a_new_version_and_keeps_the_old(client, conn):
    signal_id = create(client).json()["id"]
    r = client.put(f"/api/signals/{signal_id}", json={**VALID, "instruction": "New wording."})
    assert r.json()["version_number"] == 2
    assert conn.execute("SELECT instruction FROM signal_versions ORDER BY version_number").fetchall() == \
        [(VALID["instruction"],), ("New wording.",)]


@pytest.mark.parametrize(("model_fields", "expected"), [
    ({}, "older-model"),                     # left out: the edit keeps the signal's model
    ({"model": "gpt-5.4-mini"}, "gpt-5.4-mini"),
])
def test_edit_selects_model_for_new_version(client, conn, model_fields, expected):
    signal_id = create(client, model="older-model").json()["id"]
    response = client.put(f"/api/signals/{signal_id}",
                          json={**VALID, "instruction": "New wording.", **model_fields})
    assert response.status_code == 200
    assert response.json()["model"] == expected
    assert conn.execute("SELECT model FROM signal_versions ORDER BY version_number").fetchall() == [
        ("older-model",), (expected,),
    ]


def test_edit_can_replace_legacy_schema_and_model(client, conn):
    signal = create(client).json()
    legacy_schema = {"type": "object", "properties": {"claim": {"type": "string"}}}
    conn.execute("UPDATE signal_versions SET output_schema = %s, model = 'placeholder' WHERE id = %s",
                 (Jsonb(legacy_schema), signal["version_id"]))

    response = client.put(f"/api/signals/{signal['id']}", json=VALID)

    assert response.status_code == 200
    assert response.json()["version_number"] == 2
    assert response.json()["model"] == "gpt-6-luna"
    assert conn.execute("SELECT output_schema, model FROM signal_versions WHERE id = %s",
                        (signal["version_id"],)).fetchone() == (legacy_schema, "placeholder")


def test_edit_that_leaves_out_guidance_keeps_it_and_null_clears_it(client, conn):
    signal_id = create(client, critical_guidance="Critical when a booking is lost").json()["id"]

    renamed = client.put(f"/api/signals/{signal_id}", json={**VALID, "name": "Renamed"}).json()
    assert renamed["critical_guidance"] == "Critical when a booking is lost"
    assert renamed["version_number"] == 1                  # nothing in the definition changed

    cleared = client.put(f"/api/signals/{signal_id}", json={**VALID, "critical_guidance": None}).json()
    assert cleared["critical_guidance"] is None and cleared["version_number"] == 2


def test_unchanged_save_makes_no_version(client, conn):
    signal_id = create(client).json()["id"]
    assert client.put(f"/api/signals/{signal_id}", json=VALID).json()["version_number"] == 1
    assert versions(conn) == 1


def test_rename_only_changes_the_name(client, conn):
    signal_id = create(client).json()["id"]
    body = client.put(f"/api/signals/{signal_id}", json={**VALID, "name": "Invented facts"}).json()
    assert (body["name"], body["version_number"]) == ("Invented facts", 1)
    assert versions(conn) == 1


def test_rename_to_a_taken_name_is_409(client):
    create(client)
    other = create(client, name="Other").json()["id"]
    assert client.put(f"/api/signals/{other}", json=VALID).status_code == 409


def test_template_cannot_be_changed_by_an_edit(client):
    signal_id = create(client).json()["id"]
    assert client.put(f"/api/signals/{signal_id}", json={**VALID, "template": "x"}).status_code == 422


# ------------------------------------------------------------------ on / off


def test_turn_off_and_on(client):
    signal_id = create(client).json()["id"]
    assert client.patch(f"/api/signals/{signal_id}", json={"active": False}).json()["active"] is False
    assert client.patch(f"/api/signals/{signal_id}", json={"active": True}).json()["active"] is True


def test_turning_off_a_missing_signal_is_404(client):
    assert client.patch("/api/signals/999", json={"active": False}).status_code == 404


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}

# ------------------------------------------------------------------ lists, events, runs


def add_turn(conn, version_id, *, events=0, hours_ago=1, severity="warning", cost=0.01, status="done",
             payload=None, error=None):
    """A trace that ended `hours_ago`, a run of `version_id` on it, and `events` events."""
    project = conn.execute("SELECT id FROM projects").fetchone()[0]
    trace_id, laminar_id = conn.execute(
        """INSERT INTO traces (project_id, laminar_trace_id, started_at, ended_at, status)
           VALUES (%s, gen_random_uuid(), now() - make_interval(hours => %s),
                   now() - make_interval(hours => %s), 'success')
           RETURNING id, laminar_trace_id::text""", (project, hours_ago, hours_ago)).fetchone()
    run_id = conn.execute(
        """INSERT INTO signal_runs (trace_id, signal_version_id, status, attempts, finished_at, cost_usd,
                                    last_error, lease_until)
           VALUES (%s, %s, %s, 1, CASE WHEN %s IN ('done', 'failed') THEN now() END, %s, %s,
                   CASE WHEN %s = 'running' THEN now() + interval '1 minute' END)
           RETURNING id""",
        (trace_id, version_id, status, status, cost, error, status)).fetchone()[0]
    for i in range(events):
        conn.execute(
            """INSERT INTO signal_events (run_id, trace_id, signal_version_id, finding_index, severity,
                                          judge_input, evidence_labels, payload)
               VALUES (%s, %s, %s, %s, %s, 'input', %s, %s)""",
            (run_id, trace_id, version_id, i, severity, [f"S{i + 1}"], Jsonb(payload or {})))
    return laminar_id, run_id


def test_list_counts_activity_in_the_range(client, conn):
    version_id = create(client).json()["version_id"]
    add_turn(conn, version_id, events=2)
    add_turn(conn, version_id, events=1, severity="info")
    add_turn(conn, version_id, events=1, hours_ago=24 * 10)       # older than the default 7 days
    [summary] = client.get("/api/signals").json()
    assert (summary["runs"], summary["events"], summary["problem_events"]) == (2, 3, 2)
    assert summary["last_event_at"] is not None


def test_events_newest_first_with_laminar_links(client, conn):
    conn.execute("UPDATE projects SET laminar_project_id = '11111111-1111-1111-1111-111111111111'")
    signal = create(client).json()
    old_trace, _ = add_turn(conn, signal["version_id"], events=1, hours_ago=5)
    new_trace, _ = add_turn(conn, signal["version_id"], events=2, hours_ago=1)
    events = client.get(f"/api/signals/{signal['id']}/events").json()["events"]
    assert [e["laminar_trace_id"] for e in events] == [new_trace, new_trace, old_trace]
    assert events[0]["laminar_url"] == \
        f"http://laminar.test:5667/project/11111111-1111-1111-1111-111111111111/traces/{new_trace}"
    assert events[0]["evidence_labels"] == ["S2"] and events[0]["cluster_id"] is None


def test_events_respect_the_limit_and_other_signals(client, conn):
    first = create(client).json()
    other = create(client, name="Other").json()
    add_turn(conn, first["version_id"], events=3)
    add_turn(conn, other["version_id"], events=2)
    page = client.get(f"/api/signals/{first['id']}/events?limit=2").json()
    assert (len(page["events"]), page["total"]) == (2, 3)
    assert client.get(f"/api/signals/{other['id']}/events").json()["total"] == 2


def test_runs_show_events_found_and_cost(client, conn):
    signal = create(client).json()
    add_turn(conn, signal["version_id"], events=2, cost=0.02)
    [run] = client.get(f"/api/signals/{signal['id']}/runs").json()["runs"]
    assert (run["status"], run["events"], run["cost_usd"], run["laminar_url"]) == ("done", 2, 0.02, None)


def test_events_and_runs_of_a_missing_signal_are_404(client):
    assert client.get("/api/signals/999/events").status_code == 404
    assert client.get("/api/signals/999/runs").status_code == 404


# ------------------------------------------------------------------ test on a sample trace


def fake_judge(outcome):
    def judge_fn(laminar, trace, version):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return lambda: judge_fn


@pytest.mark.parametrize(("model_fields", "expected"), [
    ({}, "gpt-6-luna"),
    ({"model": "gpt-5.4-mini"}, "gpt-5.4-mini"),
])
def test_run_returns_findings_and_writes_nothing(client, conn, model_fields, expected):
    version_id = create(client).json()["version_id"]
    laminar_id, _ = add_turn(conn, version_id)
    runs_before = conn.execute("SELECT count(*) FROM signal_runs").fetchone()[0]
    judge_fn = Mock(return_value=JudgeResult(
        findings=[Finding("critical", ["span-9"], {"claim": "17.3 miles"}, ["S4"])], analysis="why",
        raw_output="{}", judge_input="trace", dropped_findings=0, model="m", usage=Usage(10, 0, 5, 0.001)))
    app.dependency_overrides[get_judge] = lambda: judge_fn
    r = client.post("/api/test-run", json={
        "laminar_trace_id": laminar_id, "definition": {**VALID, **model_fields},
    })
    assert r.status_code == 200
    assert judge_fn.call_args.args[2].model == expected
    assert r.json()["findings"] == [{"severity": "critical", "fields": {"claim": "17.3 miles"},
                                     "evidence_labels": ["S4"], "span_ids": ["span-9"]}]
    assert conn.execute("SELECT count(*) FROM signal_runs").fetchone()[0] == runs_before
    assert conn.execute("SELECT count(*) FROM signal_events").fetchone()[0] == 0


def test_run_on_an_unknown_trace_is_404(client):
    r = client.post("/api/test-run", json={"laminar_trace_id": str(uuid.uuid4()), "definition": VALID})
    assert r.status_code == 404


def test_run_with_a_bad_trace_id_is_422(client):
    assert client.post("/api/test-run", json={"laminar_trace_id": "nope", "definition": VALID}).status_code == 422


def test_run_when_the_judge_is_unavailable_is_503(client, conn):
    laminar_id, _ = add_turn(conn, create(client).json()["version_id"])
    app.dependency_overrides[get_judge] = fake_judge(JudgeError("429", retryable=True, uses_attempt=False))
    r = client.post("/api/test-run", json={"laminar_trace_id": laminar_id, "definition": VALID})
    assert r.status_code == 503


# ------------------------------------------------------------------ filters, paging, counts, clusters, Home
# Every number the UI shows is counted in SQL, so these check that totals and charts match the
# lists, whatever the filters, and that paging never changes a total.


def event_ids(conn, laminar_id):
    return [r[0] for r in conn.execute(
        """SELECT e.id FROM signal_events e JOIN traces t ON t.id = e.trace_id
           WHERE t.laminar_trace_id = %s ORDER BY e.id""", (laminar_id,)).fetchall()]


def add_cluster(conn, signal_id, event_ids_, *, name="Wrong park", level=0):
    """A cluster of `signal_id` holding the given events (each gets a zero embedding first)."""
    cluster_id = conn.execute(
        "INSERT INTO clusters (signal_id, name, level) VALUES (%s, %s, %s) RETURNING id",
        (signal_id, name, level)).fetchone()[0]
    for event_id in event_ids_:
        conn.execute("INSERT INTO event_embeddings (event_id, model, embedding) VALUES (%s, 'm', %s)",
                     (event_id, "[" + ",".join(["0"] * 1536) + "]"))
        conn.execute("INSERT INTO cluster_members (event_id, cluster_id) VALUES (%s, %s)", (event_id, cluster_id))
    return cluster_id


def test_events_filter_by_severity_cluster_and_search_and_total_ignores_paging(client, conn):
    signal = create(client).json()
    url = f"/api/signals/{signal['id']}/events"
    trace, _ = add_turn(conn, signal["version_id"], events=3, severity="critical", payload={"claim": "Glacier Bay"})
    add_turn(conn, signal["version_id"], events=2, severity="info", payload={"claim": "sold out"})
    cluster = add_cluster(conn, signal["id"], event_ids(conn, trace)[:2])

    assert client.get(url, params={"severity": "critical"}).json()["total"] == 3
    assert client.get(url, params={"cluster": cluster}).json()["total"] == 2
    assert client.get(url, params={"q": "glacier"}).json()["total"] == 3            # field value, any case
    assert client.get(url, params={"q": "claim"}).json()["total"] == 0              # field names don't match
    assert client.get(url, params={"q": trace[:8]}).json()["total"] == 3            # part of the trace ID
    assert client.get(url, params={"q": "S2"}).json()["total"] == 2                 # evidence labels
    page = client.get(url, params={"limit": 2, "offset": 4}).json()
    assert (page["total"], len(page["events"])) == (5, 1)


def test_search_treats_like_wildcards_as_plain_text(client, conn):
    signal = create(client).json()
    add_turn(conn, signal["version_id"], events=1, payload={"claim": "100% booked"})
    add_turn(conn, signal["version_id"], events=1, payload={"claim": "1000 booked"})
    url = f"/api/signals/{signal['id']}/events"
    assert client.get(url, params={"q": "100%"}).json()["total"] == 1
    assert client.get(url, params={"q": "_"}).json()["total"] == 0


def test_bad_severity_is_422(client, conn):
    signal = create(client).json()
    assert client.get(f"/api/signals/{signal['id']}/events?severity=loud").status_code == 422


def test_event_counts_match_the_list_and_split_by_cluster(client, conn):
    signal = create(client).json()
    trace, _ = add_turn(conn, signal["version_id"], events=3, hours_ago=2)
    add_turn(conn, signal["version_id"], events=1, hours_ago=30)
    cluster = add_cluster(conn, signal["id"], event_ids(conn, trace)[:2])
    counts = client.get(f"/api/signals/{signal['id']}/event-counts",
                        params={"bucket_minutes": 60}).json()
    assert sum(c["events"] for c in counts) == client.get(f"/api/signals/{signal['id']}/events").json()["total"]
    newest = {c["cluster_id"]: c["events"] for c in counts if c["bucket"] == counts[-1]["bucket"]}
    assert newest == {cluster: 2, None: 1}
    starts = [datetime.fromisoformat(c["bucket"]) for c in counts]
    assert all(b.minute == 0 and b.second == 0 for b in starts)                     # aligned to the hour


def test_too_many_buckets_is_422(client, conn):
    signal = create(client).json()
    r = client.get(f"/api/signals/{signal['id']}/event-counts", params={"bucket_minutes": 1})  # 7 days
    assert r.status_code == 422


def test_runs_filter_and_run_counts_count_only_finished_runs(client, conn):
    signal = create(client).json()
    add_turn(conn, signal["version_id"], events=1)
    add_turn(conn, signal["version_id"])
    add_turn(conn, signal["version_id"], status="failed", error="model timed out")
    add_turn(conn, signal["version_id"], status="pending", cost=None)
    add_turn(conn, signal["version_id"], status="running", cost=None)
    url = f"/api/signals/{signal['id']}/runs"
    assert client.get(url).json()["total"] == 5
    assert client.get(url, params={"status": "pending"}).json()["total"] == 1
    assert client.get(url, params={"outcome": "event"}).json()["total"] == 1
    assert client.get(url, params={"q": "timed out"}).json()["total"] == 1
    [bucket] = client.get(f"/api/signals/{signal['id']}/run-counts", params={"bucket_minutes": 1440}).json()
    assert (bucket["event"], bucket["nothing"], bucket["failed"]) == (1, 1, 1)     # 2 unfinished left out


def test_clusters_are_counted_within_the_range_and_only_leaves(client, conn):
    signal = create(client).json()
    recent, _ = add_turn(conn, signal["version_id"], events=3, hours_ago=1, severity="critical")
    old, _ = add_turn(conn, signal["version_id"], events=2, hours_ago=24 * 10)
    add_turn(conn, signal["version_id"], events=1)                                 # not in a cluster
    near = add_cluster(conn, signal["id"], event_ids(conn, recent)[:2] + event_ids(conn, old), name="Near")
    add_cluster(conn, signal["id"], event_ids(conn, recent)[2:], name="Small")
    add_cluster(conn, signal["id"], [], name="A parent", level=1)

    body = client.get(f"/api/signals/{signal['id']}/clusters").json()             # default: past 7 days
    assert (body["events"], body["clustered_events"]) == (4, 3)
    assert [(c["id"], c["events"], c["problem_events"]) for c in body["clusters"]][0] == (near, 2, 2)
    assert [c["name"] for c in body["clusters"]] == ["Near", "Small"]


def test_list_has_cluster_count_and_activity(client, conn):
    signal = create(client).json()
    trace, _ = add_turn(conn, signal["version_id"], events=2, hours_ago=1)
    add_turn(conn, signal["version_id"], events=1, hours_ago=24 * 6)
    add_cluster(conn, signal["id"], event_ids(conn, trace))
    [summary] = client.get("/api/signals").json()
    assert summary["clusters"] == 1
    assert len(summary["activity"]) == 24 and sum(summary["activity"]) == 3
    assert summary["activity"][-1] == 2                                            # newest slice last


def test_overview_counts_problems_cost_and_finished_runs(client, conn):
    first = create(client).json()
    second = create(client, name="Other").json()
    trace, _ = add_turn(conn, first["version_id"], events=2, severity="critical", cost=0.02)
    add_turn(conn, first["version_id"], events=1, severity="info", cost=None)      # unpriced
    add_turn(conn, second["version_id"], status="pending", cost=None)              # unfinished
    cluster = add_cluster(conn, first["id"], event_ids(conn, trace))

    body = client.get("/api/overview", params={"bucket_minutes": 60}).json()
    by_name = {s["name"]: s for s in body["signals"]}
    assert (by_name["Made-up facts"]["problem_events"], by_name["Made-up facts"]["cost_usd"],
            by_name["Made-up facts"]["unpriced_runs"]) == (2, 0.02, 1)
    assert by_name["Other"]["unpriced_runs"] == 0                                   # pending isn't counted
    assert [(c["id"], c["problem_events"], c["signal_name"]) for c in body["problem_clusters"]] == \
        [(cluster, 2, "Made-up facts")]
    assert sum(c["problem_events"] for c in body["problem_counts"]) == 2
    assert sum(b["event"] + b["nothing"] + b["failed"] for b in body["run_counts"]) == 2


def test_day_buckets_start_at_local_midnight_in_the_given_zone(client, conn):
    # A turn at 03:00 UTC on Nov 12 is 19:00 on Nov 11 in Los Angeles: it belongs to LA's Nov 11.
    signal = create(client).json()
    _, _ = add_turn(conn, signal["version_id"], events=1)
    conn.execute("UPDATE traces SET started_at = '2026-11-12T02:59:00Z', ended_at = '2026-11-12T03:00:00Z'")
    window = {"bucket_minutes": 1440, "since": "2026-11-10T00:00:00Z", "until": "2026-11-14T00:00:00Z"}
    url = f"/api/signals/{signal['id']}/event-counts"
    [utc] = client.get(url, params=window).json()
    [la] = client.get(url, params={**window, "tz": "America/Los_Angeles"}).json()
    assert datetime.fromisoformat(utc["bucket"]) == datetime.fromisoformat("2026-11-12T00:00:00+00:00")
    assert datetime.fromisoformat(la["bucket"]) == datetime.fromisoformat("2026-11-11T00:00:00-08:00")


def test_daylight_saving_day_is_one_25_hour_bucket(client, conn):
    # Nov 1, 2026 in LA: clocks go back at 2 AM. 23:30 local that day is 07:30 UTC Nov 2, still Nov 1.
    signal = create(client).json()
    add_turn(conn, signal["version_id"], events=1)
    conn.execute("UPDATE traces SET started_at = '2026-11-02T07:29:00Z', ended_at = '2026-11-02T07:30:00Z'")
    [bucket] = client.get(f"/api/signals/{signal['id']}/event-counts",
                          params={"bucket_minutes": 1440, "tz": "America/Los_Angeles",
                                  "since": "2026-10-31T00:00:00Z", "until": "2026-11-04T00:00:00Z"}).json()
    assert datetime.fromisoformat(bucket["bucket"]) == datetime.fromisoformat("2026-11-01T00:00:00-07:00")


def test_unknown_time_zone_is_422(client, conn):
    signal = create(client).json()
    r = client.get(f"/api/signals/{signal['id']}/event-counts", params={"bucket_minutes": 60, "tz": "Mars/Base"})
    assert r.status_code == 422
    assert client.get("/api/overview", params={"bucket_minutes": 60, "tz": "Mars/Base"}).status_code == 422
