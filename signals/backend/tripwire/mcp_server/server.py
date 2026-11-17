"""The MCP server: two read-only tools over the Tripwire API, served over streamable HTTP."""

import logging
from typing import Annotated
from urllib.parse import urlsplit

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

from . import format
from .api import TripwireAPI
from .auth import CognitoVerifier
from .settings import READ_SCOPE, Settings, SettingsError, load

log = logging.getLogger("mcp_server")

INSTRUCTIONS = """Tripwire watches an AI agent's conversations with "signals" (an LLM judge looking for one
kind of problem) and groups similar findings into clusters. Start with list_problem_clusters to see
the biggest problems, then get_cluster for what goes wrong in one cluster, its likely causes and
example events with links to their traces in Laminar."""


def build(settings: Settings, api: TripwireAPI | None = None, verifier=None) -> MCPServer:
    api = api or TripwireAPI(settings.api_url)
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


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = load()
    except SettingsError as e:
        raise SystemExit(f"mcp_server: {e}")
    if settings.cognito:
        log.info("auth is on: Cognito pool %s, app client %s", settings.cognito.user_pool_id,
                 settings.cognito.client_id)
    else:
        log.warning("auth is off: no MCP_COGNITO_* settings. Anyone who can reach %s can read clusters.",
                    settings.public_url)
    log.info("serving %s (listening on %s:%s), reading %s", settings.public_url, settings.host,
             settings.port, settings.api_url)
    build(settings).run("streamable-http", host=settings.host, port=settings.port,
                        streamable_http_path=settings.path, transport_security=transport_security(settings))
