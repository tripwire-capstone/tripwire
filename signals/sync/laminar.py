"""Small client for Laminar's SQL API (POST /v1/sql/query, SELECT only)."""

import time

import httpx

RETRY_STATUSES = {429, 502, 503, 504}  # rate limited or briefly unavailable


class LaminarError(RuntimeError):
    """Laminar couldn't answer the query (bad query, auth, or still down after retries)."""


class LaminarSQL:
    def __init__(self, api_key, base_url="http://localhost", port=8000, *,
                 timeout=30.0, retries=3, backoff=1.0, transport=None):
        self.url = f"{base_url.rstrip('/')}:{port}/v1/sql/query"
        self.retries = retries
        self.backoff = backoff
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,  # tests pass a fake one
        )

    def query(self, sql, parameters=None):
        """Run one SELECT and return its rows as a list of dicts."""
        body = {"query": sql, "parameters": parameters or {}}
        for attempt in range(self.retries + 1):
            last_try = attempt == self.retries
            try:
                response = self._client.post(self.url, json=body)
            except httpx.TransportError as e:
                if last_try:
                    raise LaminarError(f"can't reach Laminar at {self.url}: {e}") from e
            else:
                if response.status_code == 200:
                    return response.json()["data"]
                if response.status_code not in RETRY_STATUSES or last_try:
                    raise LaminarError(f"HTTP {response.status_code}: {_error_text(response)}")
            time.sleep(self.backoff * 2 ** attempt)  # 1s, 2s, 4s

    def close(self):
        self._client.close()


def _error_text(response):
    try:
        return response.json().get("error") or response.text
    except ValueError:
        return response.text or "(empty response)"
