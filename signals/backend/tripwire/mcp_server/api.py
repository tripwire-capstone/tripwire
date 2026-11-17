"""Calls to the Tripwire API. Every failure becomes a ToolError with a message the agent can pass on."""

from datetime import datetime, timedelta, timezone

import httpx
from mcp.server.mcpserver.exceptions import ToolError


class TripwireAPI:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self._transport = transport          # tests pass an httpx.MockTransport

    async def problem_clusters(self, days: int) -> list[dict]:
        """Clusters with warning or critical events in the last `days` days, most problem events first."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        # The overview's charts aren't used here; day buckets keep them small.
        overview = await self._get("/api/overview", {"bucket_minutes": 1440, "since": since.isoformat()})
        return overview["problem_clusters"]

    async def cluster(self, cluster_id: int, examples: int) -> dict:
        return await self._get(f"/api/clusters/{cluster_id}", {"examples": examples},
                               not_found=f"No cluster {cluster_id}.")

    async def _get(self, path: str, params: dict, not_found: str | None = None) -> dict:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self._transport,
                                         timeout=20) as client:
                response = await client.get(path, params=params)
        except httpx.TransportError:
            raise ToolError(f"Can't reach the Tripwire API at {self.base_url}. Is it running?")
        if response.status_code == 404 and not_found:
            raise ToolError(not_found)
        if response.status_code != 200:
            raise ToolError(f"The Tripwire API answered {response.status_code}: {reason(response)}")
        return response.json()


def reason(response: httpx.Response) -> str:
    """The API's own explanation when it gave one, else the start of the body."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    text = detail if isinstance(detail, str) else str(detail) if detail else response.text
    return (text or response.reason_phrase or "no reason given").strip().splitlines()[0][:200]
