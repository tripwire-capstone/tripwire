"""The MCP server: settings, the two tools (against a fake API), and the Cognito token checks (with
our own signing keys in place of Cognito's). No database or network needed."""

import asyncio
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import Client
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from tripwire.mcp_server.api import TripwireAPI
from tripwire.mcp_server.auth import CognitoVerifier
from tripwire.mcp_server.server import build, transport_security
from tripwire.mcp_server.settings import Cognito, SettingsError, load

COGNITO_ENV = {"MCP_COGNITO_REGION": "us-east-1", "MCP_COGNITO_USER_POOL_ID": "us-east-1_Pool",
               "MCP_COGNITO_CLIENT_ID": "mcp-client"}
COGNITO = Cognito("us-east-1", "us-east-1_Pool", "mcp-client")

CLUSTER = {
    "id": 12, "name": "Agent cancels orders without confirmation", "signal_id": 3,
    "signal_name": "Policy violation", "events": 23, "problem_events": 21, "conversations": 9,
    "first_event_at": "2026-11-10T09:00:00Z", "last_event_at": "2026-11-16T18:00:00Z",
    "summaries": [{"event_id": 41, "summary": "Cancelled an order without asking.",
                   "cause": "The prompt never asks for confirmation."}],
    "examples": [{"id": 41, "severity": "critical", "payload": {"rule": "confirm first", "count": 2},
                  "evidence_labels": ["S4"], "analysis": "The agent called cancel_order.\n\nNo confirmation.",
                  "summary": "Cancelled an order without asking.", "cause": None,
                  "laminar_trace_id": "abc", "session_id": "s-1", "trace_ended_at": "2026-11-16T18:00:00Z",
                  "laminar_url": "http://laminar.test/project/p/traces/abc"}],
}


# ------------------------------------------------------------------ settings

def test_no_cognito_settings_means_auth_off():
    settings = load({})
    assert settings.cognito is None
    assert (settings.api_url, settings.public_url, settings.path) == \
        ("http://localhost:8080", "http://localhost:8090/mcp", "/mcp")


def test_all_cognito_settings_turn_auth_on():
    settings = load(COGNITO_ENV)
    assert settings.cognito == COGNITO
    assert settings.cognito.issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Pool"


def test_some_cognito_settings_refuse_to_start():
    with pytest.raises(SettingsError, match="missing MCP_COGNITO_CLIENT_ID"):
        load({**COGNITO_ENV, "MCP_COGNITO_CLIENT_ID": " "})


def test_public_url_must_be_a_full_url():
    with pytest.raises(SettingsError):
        load({"MCP_PUBLIC_URL": "localhost:8090/mcp"})


def test_only_its_own_host_names_are_served():
    security = transport_security(load({"MCP_PUBLIC_URL": "https://tripwire.example.com/mcp"}))
    assert security.enable_dns_rebinding_protection
    assert "tripwire.example.com" in security.allowed_hosts and "localhost:*" in security.allowed_hosts


# ------------------------------------------------------------------ tools

def call(handler, tool, arguments):
    """Calls one tool on a server whose API is `handler`; returns (is_error, text, requests made)."""
    requests = []

    def recording(request):
        requests.append(request)
        return handler(request)

    server = build(load({}), api=TripwireAPI("http://api.test", httpx.MockTransport(recording)))

    async def run():
        async with Client(server) as client:
            return await client.call_tool(tool, arguments)

    result = asyncio.run(run())
    return result.is_error, result.content[0].text, requests


def overview(clusters):
    return lambda request: httpx.Response(200, json={"signals": [], "problem_clusters": clusters,
                                                     "problem_counts": [], "run_counts": []})


def test_problem_clusters_reads_the_overview_for_the_range():
    clusters = [{"id": i, "name": f"Problem {i}", "signal_id": 1, "signal_name": "Policy",
                 "problem_events": 10 - i} for i in range(1, 4)]
    is_error, text, [request] = call(overview(clusters), "list_problem_clusters", {"days": 3, "limit": 2})
    assert not is_error
    assert request.url.path == "/api/overview"
    assert "since" in request.url.params and request.url.params["bucket_minutes"] == "1440"
    assert "**Cluster 1: Problem 1** (signal: Policy): 9 problem events" in text
    assert "Problem 3" not in text and "(2 of 3)" in text


def test_no_problem_clusters_says_so():
    _, text, _ = call(overview([]), "list_problem_clusters", {})
    assert text == "No clusters with warning or critical events in the last 7 days."


def test_get_cluster_shows_summaries_and_examples():
    is_error, text, [request] = call(lambda r: httpx.Response(200, json=CLUSTER), "get_cluster",
                                     {"cluster_id": 12, "examples": 2})
    assert not is_error
    assert (request.url.path, request.url.params["examples"]) == ("/api/clusters/12", "2")
    assert text.startswith("# Cluster 12: Agent cancels orders without confirmation")
    assert "- Cancelled an order without asking. Likely cause: The prompt never asks for confirmation." in text
    assert "### Event 41 (critical)" in text
    assert "  - rule: confirm first" in text and "  - count: 2" in text
    assert "- Open in Laminar: http://laminar.test/project/p/traces/abc" in text
    assert "> The agent called cancel_order.\n>\n> No confirmation." in text


def test_unknown_cluster_is_a_readable_error():
    is_error, text, _ = call(lambda r: httpx.Response(404, json={"detail": "cluster not found"}),
                             "get_cluster", {"cluster_id": 12})
    assert is_error and text.endswith("No cluster 12.")


def test_api_down_is_a_readable_error():
    def down(request):
        raise httpx.ConnectError("refused")
    is_error, text, _ = call(down, "list_problem_clusters", {})
    assert is_error and "Can't reach the Tripwire API at http://api.test. Is it running?" in text


def test_other_api_errors_give_the_status_and_reason():
    is_error, text, _ = call(lambda r: httpx.Response(503, json={"detail": "no Laminar project yet"}),
                             "list_problem_clusters", {})
    assert is_error and "answered 503: no Laminar project yet" in text


def test_arguments_out_of_range_are_refused():
    is_error, _, requests = call(overview([]), "list_problem_clusters", {"days": 0})
    assert is_error and requests == []


# ------------------------------------------------------------------ Cognito tokens

class Keys(jwt.PyJWKClient):
    """Stands in for Cognito's public keys: hands out our own key for every token."""

    def __init__(self, private_key):
        self.public = private_key.public_key()

    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict({**jwt.algorithms.RSAAlgorithm.to_jwk(self.public, as_dict=True),
                                    "alg": "RS256"})


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, **changes):
    claims = {"iss": COGNITO.issuer, "token_use": "access", "client_id": "mcp-client",
              "scope": "openid email tripwire-mcp/read", "sub": "user-1", "exp": int(time.time()) + 600}
    claims.update(changes)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


def verify(key, raw, signing_key=None):
    return asyncio.run(CognitoVerifier(COGNITO, Keys(signing_key or key)).verify_token(raw))


def test_a_valid_access_token_is_accepted(key):
    access = verify(key, token(key))
    assert (access.client_id, access.subject) == ("mcp-client", "user-1")
    assert "tripwire-mcp/read" in access.scopes


@pytest.mark.parametrize("changes", [
    {"token_use": "id"},                                   # an ID token, not an access token
    {"client_id": "alb-client"},                           # the Laminar UI's login
    {"scope": "openid email"},                             # no MCP scope
    {"exp": int(time.time()) - 10},                        # expired
    {"iss": "https://cognito-idp.us-east-1.amazonaws.com/other_pool"},
    {"client_id": None},                                   # missing claim
])
def test_other_tokens_are_refused(key, changes):
    assert verify(key, token(key, **changes)) is None


def test_a_token_signed_by_another_key_is_refused(key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert verify(key, token(other)) is None
    assert verify(key, "not a token") is None


# ------------------------------------------------------------------ auth over HTTP

class AcceptOne:
    """A verifier that accepts only the token "good"."""

    async def verify_token(self, raw):
        if raw != "good":
            return None
        return AccessToken(token=raw, client_id="mcp-client", scopes=["tripwire-mcp/read"],
                           expires_at=int(time.time()) + 600)


@pytest.fixture
def http_with_auth():
    settings = load(COGNITO_ENV)
    server = build(settings, api=TripwireAPI("http://api.test", httpx.MockTransport(overview([]))),
                   verifier=AcceptOne())
    app = server.streamable_http_app(streamable_http_path="/mcp", transport_security=transport_security(settings))
    with TestClient(app, base_url="http://localhost:8090") as client:
        yield client


INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "test", "version": "1"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_no_token_is_401_pointing_to_the_login(http_with_auth):
    r = http_with_auth.post("/mcp", json=INITIALIZE, headers=HEADERS)
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]


def test_metadata_names_cognito_as_the_login(http_with_auth):
    r = http_with_auth.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_servers"] == [COGNITO.issuer]
    assert "tripwire-mcp/read" in body["scopes_supported"]


def test_a_bad_token_is_401_and_a_good_one_gets_in(http_with_auth):
    bad = http_with_auth.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Authorization": "Bearer bad"})
    good = http_with_auth.post("/mcp", json=INITIALIZE, headers={**HEADERS, "Authorization": "Bearer good"})
    assert bad.status_code == 401
    assert good.status_code == 200
