"""Tests for sync/laminar.py against a fake HTTP server (no network)."""

import json

import httpx
import pytest

from sync.laminar import LaminarError, LaminarSQL


def client(handler, **kwargs):
    return LaminarSQL("test-key", "http://laminar.test", 8000,
                      transport=httpx.MockTransport(handler), backoff=0, **kwargs)


def test_sends_key_query_and_parameters_and_returns_rows():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"n": 1}]})

    rows = client(handler).query("SELECT {n:UInt8} AS n", {"n": 1})
    assert rows == [{"n": 1}]
    assert seen["url"] == "http://laminar.test:8000/v1/sql/query"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"] == {"query": "SELECT {n:UInt8} AS n", "parameters": {"n": 1}}


def test_query_error_raises_with_laminars_message():
    def handler(request):
        return httpx.Response(400, json={"error": "Query validation failed: only SELECT"})

    with pytest.raises(LaminarError, match="HTTP 400: Query validation failed: only SELECT"):
        client(handler).query("DROP TABLE spans")


def test_bad_query_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(500, json={"error": "Error executing query"})

    with pytest.raises(LaminarError):
        client(handler).query("SELECT nonsense")
    assert len(calls) == 1


def test_rate_limit_is_retried_then_succeeds():
    responses = iter([httpx.Response(429), httpx.Response(429),
                      httpx.Response(200, json={"data": []})])
    assert client(lambda request: next(responses)).query("SELECT 1") == []


def test_gives_up_after_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, text="unavailable")

    with pytest.raises(LaminarError, match="HTTP 503"):
        client(handler, retries=2).query("SELECT 1")
    assert len(calls) == 3  # first try + 2 retries


def test_connection_failure_raises_laminar_error():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LaminarError, match="can't reach Laminar"):
        client(handler, retries=1).query("SELECT 1")
