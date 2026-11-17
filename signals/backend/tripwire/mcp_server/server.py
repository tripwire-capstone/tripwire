"""The MCP server: two read-only tools over the Tripwire API, served inside the API at /api/mcp.

The tools call the API in-process (no network hop), through the same routes the UI uses.
"""

import logging
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from . import format
from .api import TripwireAPI
from .auth import CognitoVerifier
from .settings import READ_SCOPE, Settings, load

log = logging.getLogger("mcp_server")

# Where MCP is served inside the API, below any path prefix (on AWS: /tripwire/api/mcp)
MCP_PATH = "/api/mcp"
# Where clients find which login to use (RFC 9728); always at the host's root
LOGIN_DOCUMENT_PREFIX = "/.well-known/oauth-protected-resource"

INSTRUCTIONS = """Tripwire watches an AI agent's conversations with "signals" (an LLM judge looking for one
kind of problem) and groups similar findings into clusters. Start with list_problem_clusters to see
the biggest problems, then get_cluster for what goes wrong in one cluster, its likely causes and
example events with links to their traces in Laminar."""


def build(settings: Settings, api: TripwireAPI, verifier=None) -> MCPServer:
    auth = None
    if settings.cognito:
        verifier = verifier or CognitoVerifier(settings.cognito)
        auth = AuthSettings(issuer_url=settings.cognito.issuer, resource_server_url=settings.public_url,
                            required_scopes=[READ_SCOPE],
                            # Cognito doesn't put the resource in its tokens; the verifier checks the
                            # app client and scope instead.
                            validate_token_resource=False)
    server = MCPServer("tripwire", instructions=INSTRUCTIONS, token_verifier=verifier if auth else None,
                       auth=auth)

    @server.tool()
    async def list_problem_clusters(
            days: Annotated[int, Field(ge=1, le=90, description="How many days back to look")] = 7,
            limit: Annotated[int, Field(ge=1, le=50, description="How many clusters to return")] = 10,
    ) -> str:
        """The clusters with the most warning or critical events in a recent time range, across every
        signal, biggest first. Each line has the cluster's ID for get_cluster."""
        return format.problem_clusters(await api.problem_clusters(days), days, limit)

    @server.tool()
    async def get_cluster(
            cluster_id: Annotated[int, Field(description="The cluster's ID, from list_problem_clusters")],
            examples: Annotated[int, Field(ge=1, le=10, description="How many example events")] = 3,
    ) -> str:
        """One cluster of similar problems: how often and since when it happens, a one-line summary and
        likely cause per event, and example events (fields, the judge's analysis, Laminar link),
        picked from different conversations where possible."""
        return format.cluster(await api.cluster(cluster_id, examples))

    return server


def transport_security(settings: Settings) -> TransportSecuritySettings:
    """Only requests addressed to this server's own host names are served, so a web page in the
    user's browser can't reach a local server through a look-alike domain (DNS rebinding)."""
    public = urlsplit(settings.public_url)
    hosts = ["localhost:*", "127.0.0.1:*", "[::1]:*", public.netloc]
    origins = ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*",
               f"{public.scheme}://{public.netloc}"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                     allowed_origins=origins)


def route_path(scope) -> str:
    """The request path below any prefix the app is mounted under."""
    path, root = scope["path"], scope.get("root_path", "")
    return path[len(root):] if root and path.startswith(root) else path


def with_mcp(api_app, settings: Settings | None = None, verifier=None):
    """`api_app` with the MCP server answering at /api/mcp (and its login document); every other
    request goes to `api_app` unchanged."""
    settings = settings or load()
    if settings.cognito is None and not settings.is_local:
        log.error("MCP is not served: %s isn't a local address and auth is off (no MCP_COGNITO_* settings)",
                  settings.public_url)
        return api_app
    if settings.cognito:
        log.info("MCP at %s, auth on: Cognito pool %s, app client %s", settings.public_url,
                 settings.cognito.user_pool_id, settings.cognito.client_id)
    else:
        log.warning("MCP at %s, auth off: anyone who can reach this address can read clusters",
                    settings.public_url)

    api = TripwireAPI("http://tripwire", httpx.ASGITransport(app=api_app))
    mcp_app = build(settings, api, verifier).streamable_http_app(
        streamable_http_path=MCP_PATH, transport_security=transport_security(settings))

    async def app(scope, receive, send):
        # The MCP app's startup and shutdown run its session manager; the API has none of its own
        if scope["type"] == "lifespan":
            await mcp_app(scope, receive, send)
            return
        path = route_path(scope)
        if scope["type"] == "http" and (path == MCP_PATH or path.startswith(LOGIN_DOCUMENT_PREFIX)):
            await mcp_app(scope, receive, send)
            return
        await api_app(scope, receive, send)

    return app
