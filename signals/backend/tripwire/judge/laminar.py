"""Laminar SQL client for the judge's deadline-bound trace fetches."""

import asyncio

import httpx

from ..poller.laminar import LaminarError, _error_text


class LaminarUnavailable(LaminarError):
    """Laminar is temporarily unable to answer the query."""


class JudgeLaminarSQL:
    """Laminar SQL client with caller-supplied deadlines and no retries."""

    def __init__(self, api_key, api_url, *, transport=None):
        self.url = f"{api_url.rstrip('/')}/v1/sql/query"
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=None,  # The caller's deadline bounds the HTTP request.
            transport=transport,
        )

    async def query(self, sql, parameters=None, *, deadline: float):
        """Run one SELECT by an absolute deadline from the event loop's clock."""
        if deadline <= asyncio.get_running_loop().time():
            raise TimeoutError("Laminar query deadline expired")

        async with asyncio.timeout_at(deadline):
            try:
                response = await self._client.post(
                    self.url, json={"query": sql, "parameters": parameters or {}}
                )
            except httpx.TransportError as exc:
                raise LaminarUnavailable("Could not reach Laminar") from exc

            status = response.status_code
            if status == 429 or 500 <= status < 600:
                raise LaminarUnavailable(f"Laminar returned HTTP {status}")
            if status != 200:
                raise LaminarError(f"HTTP {status}: {_error_text(response)}")
            return response.json()["data"]

    async def aclose(self):
        await self._client.aclose()
