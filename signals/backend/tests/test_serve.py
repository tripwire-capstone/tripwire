from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from tripwire.api.app import app as api_app
from tripwire.api.db import get_conn
from tripwire.api.serve import build_app, normalize_base_path, page_with_base
from tripwire.mcp_server.server import with_mcp
from tripwire.mcp_server.settings import load

INDEX = '<html><head><base href="/" /></head><body><div id="root"></div></body></html>'


@pytest.fixture
def ui_dir(tmp_path):
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('ui');")
    (tmp_path.parent / "secret.txt").write_text("outside the UI folder")
    return tmp_path


@pytest.fixture(autouse=True)
def no_database():
    # /api/health runs SELECT 1; these tests are about routing, not the database
    api_app.dependency_overrides[get_conn] = lambda: Mock()
    yield
    api_app.dependency_overrides.clear()


def test_normalize_base_path_accepts_any_slashes():
    assert normalize_base_path("tripwire") == "/tripwire"
    assert normalize_base_path("/tripwire/") == "/tripwire"
    assert normalize_base_path(" /tripwire ") == "/tripwire"
    assert normalize_base_path("/") == ""
    assert normalize_base_path("") == ""


def test_page_with_base_points_the_base_tag_at_the_prefix():
    assert '<base href="/tripwire/" />' in page_with_base(INDEX, "/tripwire")
    assert '<base href="/" />' in page_with_base(INDEX, "")


def test_page_with_base_refuses_a_page_without_the_base_tag():
    with pytest.raises(ValueError):
        page_with_base("<html></html>", "/tripwire")


def test_serve_reaches_the_api_under_the_prefix(ui_dir):
    client = TestClient(build_app(ui_dir, "/tripwire"))
    response = client.get("/tripwire/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_serve_api_sees_paths_without_the_prefix(ui_dir):
    client = TestClient(build_app(ui_dir, "/tripwire"))
    assert client.get("/tripwire/api/no-such-route").status_code == 404
    assert client.get("/tripwire/api/no-such-route").json() == {"detail": "Not Found"}


def test_serve_sends_ui_files_under_the_prefix(ui_dir):
    client = TestClient(build_app(ui_dir, "/tripwire"))
    response = client.get("/tripwire/assets/app.js")
    assert response.status_code == 200
    assert "console.log('ui');" in response.text


def test_serve_sends_the_page_with_the_prefix_in_its_base_tag(ui_dir):
    client = TestClient(build_app(ui_dir, "/tripwire"))
    response = client.get("/tripwire/")
    assert response.status_code == 200
    assert '<base href="/tripwire/" />' in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_serve_sends_the_page_for_any_ui_address(ui_dir):
    client = TestClient(build_app(ui_dir, "/tripwire"))
    response = client.get("/tripwire/signals/3?tab=events")
    assert response.status_code == 200
    assert '<base href="/tripwire/" />' in response.text


def test_serve_redirects_the_bare_prefix_to_the_slash(ui_dir):
    client = TestClient(build_app(ui_dir, "/tripwire"), follow_redirects=False)
    response = client.get("/tripwire")
    assert response.status_code in (307, 308)
    assert response.headers["location"].endswith("/tripwire/")


def test_serve_never_sends_files_outside_the_ui_folder(ui_dir):
    client = TestClient(build_app(ui_dir, "/tripwire"))
    response = client.get("/tripwire/../secret.txt")
    assert "outside the UI folder" not in response.text


def test_serve_ignores_paths_outside_the_prefix(ui_dir):
    client = TestClient(build_app(ui_dir, "/tripwire"))
    assert client.get("/api/health").status_code == 404
    assert client.get("/").status_code == 404


def test_serve_without_a_prefix_serves_at_the_root(ui_dir):
    client = TestClient(build_app(ui_dir, ""))
    assert client.get("/api/health").json() == {"ok": True}
    assert '<base href="/" />' in client.get("/signals").text
    assert client.get("/assets/app.js").status_code == 200


def test_serve_without_a_built_ui_still_serves_the_api(tmp_path):
    client = TestClient(build_app(tmp_path / "missing", "/tripwire"))
    assert client.get("/tripwire/api/health").json() == {"ok": True}
    assert client.get("/tripwire/").status_code == 404



# ------------------------------------------------------------------ MCP inside the API

MCP_ENV = {"MCP_COGNITO_REGION": "us-east-1", "MCP_COGNITO_USER_POOL_ID": "us-east-1_Pool",
           "MCP_COGNITO_CLIENT_ID": "mcp-client", "MCP_PUBLIC_URL": "https://laminar.example.com/tripwire/api/mcp"}


def with_mcp_app(env):
    """A fresh API-with-MCP (each one's MCP session manager can only start once)."""
    return with_mcp(api_app, load(env))


def test_serve_passes_the_mcp_login_document_outside_the_prefix_to_the_api(ui_dir):
    # MCP clients ask for it at the host's root, outside /tripwire: it must not get the UI or a 404
    with TestClient(build_app(ui_dir, "/tripwire", with_mcp_app(MCP_ENV)),
                    base_url="https://laminar.example.com") as client:
        response = client.get("/.well-known/oauth-protected-resource/tripwire/api/mcp")
    assert response.status_code == 200
    assert response.json()["resource"] == "https://laminar.example.com/tripwire/api/mcp"


def test_serve_reaches_mcp_under_the_prefix(ui_dir):
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                             "clientInfo": {"name": "test", "version": "1"}}}
    with TestClient(build_app(ui_dir, "/tripwire", with_mcp_app({})), base_url="http://localhost:8080") as client:
        response = client.post("/tripwire/api/mcp", json=initialize,
                               headers={"Accept": "application/json, text/event-stream"})
        assert response.status_code == 200
        assert client.get("/tripwire/api/health").json() == {"ok": True}
