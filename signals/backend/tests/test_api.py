"""The signals API against a real Postgres."""

import pytest
from fastapi.testclient import TestClient

from tripwire.api.app import app
from tripwire.api.db import get_conn

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


def test_model_defaults_to_the_env_var(client, monkeypatch):
    monkeypatch.setenv("JUDGE_DEFAULT_MODEL", "judge-model-x")
    assert create(client).json()["model"] == "judge-model-x"


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
    