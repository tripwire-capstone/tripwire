import uuid
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
    ({}, "gpt-6-luna"),
    ({"model": "gpt-5.4-mini"}, "gpt-5.4-mini"),
])
def test_edit_selects_model_for_new_version(client, conn, model_fields, expected):
    signal_id = create(client, model="older-model").json()["id"]
    response = client.put(f"/api/signals/{signal_id}", json={**VALID, **model_fields})
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


def add_turn(conn, version_id, *, events=0, hours_ago=1, severity="warning", cost=0.01):
    """A trace that ended `hours_ago`, a done run of `version_id` on it, and `events` events."""
    project = conn.execute("SELECT id FROM projects").fetchone()[0]
    trace_id, laminar_id = conn.execute(
        """INSERT INTO traces (project_id, laminar_trace_id, started_at, ended_at, status)
           VALUES (%s, gen_random_uuid(), now() - make_interval(hours => %s),
                   now() - make_interval(hours => %s), 'success')
           RETURNING id, laminar_trace_id::text""", (project, hours_ago, hours_ago)).fetchone()
    run_id = conn.execute(
        """INSERT INTO signal_runs (trace_id, signal_version_id, status, attempts, finished_at, cost_usd)
           VALUES (%s, %s, 'done', 1, now(), %s) RETURNING id""", (trace_id, version_id, cost)).fetchone()[0]
    for i in range(events):
        conn.execute(
            """INSERT INTO signal_events (run_id, trace_id, signal_version_id, finding_index, severity,
                                          judge_input, evidence_labels)
               VALUES (%s, %s, %s, %s, %s, 'input', %s)""",
            (run_id, trace_id, version_id, i, severity, [f"S{i + 1}"]))
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
    events = client.get(f"/api/signals/{signal['id']}/events").json()
    assert [e["laminar_trace_id"] for e in events] == [new_trace, new_trace, old_trace]
    assert events[0]["laminar_url"] == \
        f"http://laminar.test:5667/project/11111111-1111-1111-1111-111111111111/traces/{new_trace}"
    assert events[0]["evidence_labels"] == ["S2"] and events[0]["cluster_id"] is None


def test_events_respect_the_limit_and_other_signals(client, conn):
    first = create(client).json()
    other = create(client, name="Other").json()
    add_turn(conn, first["version_id"], events=3)
    add_turn(conn, other["version_id"], events=2)
    assert len(client.get(f"/api/signals/{first['id']}/events?limit=2").json()) == 2
    assert len(client.get(f"/api/signals/{other['id']}/events").json()) == 2


def test_runs_show_events_found_and_cost(client, conn):
    signal = create(client).json()
    add_turn(conn, signal["version_id"], events=2, cost=0.02)
    [run] = client.get(f"/api/signals/{signal['id']}/runs").json()
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
