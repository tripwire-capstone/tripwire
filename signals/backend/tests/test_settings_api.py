"""Tests for the Settings page endpoints (tripwire/api/settings.py) and their key checks."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from tripwire.api.app import app
from tripwire.api.db import get_conn
from tripwire.api.settings import CheckUnavailable, KeyChecks, KeyRejected, get_key_checks

SECRET = "ab" * 32
LAMINAR_PROJECT = "42fc1e36-6c0e-4a69-9850-b0e849bb4656"


class FakeChecks:
    """Answers like Laminar and OpenAI would, and records what it was asked."""

    def __init__(self, error=None):
        self.error = error
        self.asked = []

    def laminar(self, api_url, api_key):
        self.asked.append(("laminar", api_url, api_key))
        if self.error:
            raise self.error
        return LAMINAR_PROJECT

    def openai(self, api_key):
        self.asked.append(("openai", api_key))
        if self.error:
            raise self.error


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TRIPWIRE_SECRET_KEY", SECRET)
    monkeypatch.setenv("LMNR_PROJECT_API_KEY", "env-laminar-key-0000")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai-key-1111")
    monkeypatch.delenv("LMNR_PROJECT_ID", raising=False)


@pytest.fixture
def checks():
    return FakeChecks()


@pytest.fixture
def client(conn, env, checks):
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_key_checks] = lambda: checks
    yield TestClient(app)
    app.dependency_overrides.clear()


# ------------------------------------------------------------------ GET /api/settings

def test_settings_shows_environment_keys_as_hints_only(client):
    body = client.get("/api/settings").json()
    assert body["connected"] is True
    assert body["laminar"] == {"source": "environment", "hint": "env-...0000", "unreadable": False,
                               "project_id": None, "project_url": None}
    assert body["openai"] == {"source": "environment", "hint": "sk-e...1111", "unreadable": False}


def test_settings_with_a_key_missing_is_not_connected(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    body = client.get("/api/settings").json()
    assert body["connected"] is False and body["openai"]["source"] == "missing"


def test_settings_never_returns_a_key(client):
    client.put("/api/settings/openai", json={"api_key": "sk-saved-key-2222222"})
    text = client.get("/api/settings").text
    assert "sk-saved-key-2222222" not in text and "env-laminar-key-0000" not in text


# ------------------------------------------------------------------ PUT /api/settings/{kind}

def test_settings_saves_a_laminar_key_and_its_project(client, conn, checks):
    response = client.put("/api/settings/laminar", json={"api_key": "  lmnr-saved-key-3333 "})
    assert response.status_code == 200
    assert checks.asked == [("laminar", "http://laminar.test:8000", "lmnr-saved-key-3333")]
    laminar = response.json()["laminar"]
    assert (laminar["source"], laminar["hint"], laminar["project_id"]) == \
        ("saved", "lmnr...3333", LAMINAR_PROJECT)
    assert laminar["project_url"] == f"http://laminar.test:5667/project/{LAMINAR_PROJECT}/traces"
    assert conn.execute("SELECT laminar_project_id::text FROM projects").fetchone()[0] == LAMINAR_PROJECT


def test_settings_saves_an_openai_key(client, checks):
    response = client.put("/api/settings/openai", json={"api_key": "sk-saved-key-2222222"})
    assert response.status_code == 200
    assert checks.asked == [("openai", "sk-saved-key-2222222")]
    assert response.json()["openai"] == {"source": "saved", "hint": "sk-s...2222", "unreadable": False}


def test_settings_rejected_key_is_not_saved(client, conn, checks):
    checks.error = KeyRejected("Laminar rejected this key.")
    response = client.put("/api/settings/laminar", json={"api_key": "lmnr-wrong-key-4444"})
    assert (response.status_code, response.json()["detail"]) == (422, "Laminar rejected this key.")
    assert conn.execute("SELECT laminar_api_key_encrypted FROM projects").fetchone()[0] is None


def test_settings_unreachable_provider_saves_nothing(client, conn, checks):
    checks.error = CheckUnavailable("can't reach OpenAI to check the key")
    response = client.put("/api/settings/openai", json={"api_key": "sk-saved-key-2222222"})
    assert response.status_code == 502
    assert response.json()["detail"] == \
        "Couldn't check the key, so it wasn't saved: can't reach OpenAI to check the key"
    assert conn.execute("SELECT openai_api_key_encrypted FROM projects").fetchone()[0] is None


@pytest.mark.parametrize("api_key", ["", "   ", "two words", "x" * 1001])
def test_settings_refuses_something_that_is_not_a_key_before_checking(client, checks, api_key):
    response = client.put("/api/settings/openai", json={"api_key": api_key})
    assert response.status_code == 422 and checks.asked == []


def test_settings_unknown_kind_is_invalid(client):
    assert client.put("/api/settings/slack", json={"api_key": "x"}).status_code == 422


def test_settings_without_a_secret_key_explains_and_saves_nothing(client, conn, monkeypatch):
    monkeypatch.delenv("TRIPWIRE_SECRET_KEY")
    response = client.put("/api/settings/openai", json={"api_key": "sk-saved-key-2222222"})
    assert response.status_code == 503 and "TRIPWIRE_SECRET_KEY" in response.json()["detail"]
    assert conn.execute("SELECT openai_api_key_encrypted FROM projects").fetchone()[0] is None


def test_settings_with_a_changed_secret_key_says_the_saved_key_is_unreadable(client, monkeypatch):
    client.put("/api/settings/openai", json={"api_key": "sk-saved-key-2222222"})
    monkeypatch.setenv("TRIPWIRE_SECRET_KEY", "cd" * 32)
    openai_key = client.get("/api/settings").json()["openai"]
    assert (openai_key["source"], openai_key["unreadable"]) == ("environment", True)


# ------------------------------------------------------------------ DELETE /api/settings/{kind}

def test_settings_clearing_a_key_falls_back_to_the_environment(client):
    client.put("/api/settings/openai", json={"api_key": "sk-saved-key-2222222"})
    response = client.delete("/api/settings/openai")
    assert response.status_code == 200 and response.json()["openai"]["source"] == "environment"


def test_settings_clearing_the_laminar_key_restores_the_deploys_project(client, conn, monkeypatch):
    monkeypatch.setenv("LMNR_PROJECT_ID", "11111111-1111-1111-1111-111111111111")
    client.put("/api/settings/laminar", json={"api_key": "lmnr-saved-key-3333"})
    laminar = client.delete("/api/settings/laminar").json()["laminar"]
    assert laminar["project_id"] == "11111111-1111-1111-1111-111111111111"


# ------------------------------------------------------------------ KeyChecks against fake providers

def laminar_answering(project_status=200, query_status=200, project_body=None):
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path, request.headers["Authorization"]))
        if request.url.path == "/v1/project":
            return httpx.Response(project_status, json=project_body or {"projectId": LAMINAR_PROJECT})
        assert json.loads(request.content)["query"] == "SELECT 1"
        return httpx.Response(query_status, json={"data": [{"1": 1}]})

    return KeyChecks(transport=httpx.MockTransport(handle)), seen


def test_key_checks_laminar_returns_the_keys_project_after_a_query():
    checks, seen = laminar_answering()
    assert checks.laminar("http://laminar.test:8000/", "lmnr-key") == LAMINAR_PROJECT
    assert seen == [("GET", "/v1/project", "Bearer lmnr-key"), ("POST", "/v1/sql/query", "Bearer lmnr-key")]


@pytest.mark.parametrize("status", [401, 403])
def test_key_checks_laminar_rejects_a_key_laminar_refuses(status):
    checks, _ = laminar_answering(project_status=status)
    with pytest.raises(KeyRejected, match="Project API Keys"):
        checks.laminar("http://laminar.test:8000", "wrong")


def test_key_checks_laminar_rejects_a_key_that_cannot_query():
    checks, _ = laminar_answering(query_status=403)
    with pytest.raises(KeyRejected):
        checks.laminar("http://laminar.test:8000", "lmnr-key")


def test_key_checks_laminar_server_error_is_unavailable_not_rejected():
    checks, _ = laminar_answering(project_status=500)
    with pytest.raises(CheckUnavailable, match="HTTP 500"):
        checks.laminar("http://laminar.test:8000", "lmnr-key")


def test_key_checks_laminar_unreachable_is_unavailable():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(CheckUnavailable, match="can't reach Laminar"):
        KeyChecks(transport=httpx.MockTransport(refuse)).laminar("http://laminar.test:8000", "lmnr-key")


def openai_answering(status):
    def handle(request):
        assert request.url.path.endswith("/models")
        return httpx.Response(status, json={"object": "list", "data": []} if status == 200
                              else {"error": {"message": "no", "type": "x"}})

    return KeyChecks(transport=httpx.MockTransport(handle))


def test_key_checks_openai_accepts_a_key_that_lists_models():
    openai_answering(200).openai("sk-key")


def test_key_checks_openai_rejects_a_wrong_key():
    with pytest.raises(KeyRejected, match="OpenAI rejected this key"):
        openai_answering(401).openai("sk-wrong")


def test_key_checks_openai_rejects_a_key_without_model_access():
    with pytest.raises(KeyRejected, match="isn't allowed to list models"):
        openai_answering(403).openai("sk-restricted")


def test_key_checks_openai_server_error_is_unavailable():
    with pytest.raises(CheckUnavailable, match="HTTP 500"):
        openai_answering(500).openai("sk-key")
