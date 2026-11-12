import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from tripwire.judge.judge import _prepare_judge_input
from tripwire.judge.laminar import JudgeLaminarSQL, LaminarUnavailable
from tripwire.judge.types import JudgeError, SignalVersion, TraceRow
from tripwire.poller.laminar import LaminarError


class JudgeFetchTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, handler):
        now = datetime.now(timezone.utc)
        trace = TraceRow(1, "trace-1", None, now, now, "success")
        version = SignalVersion(None, None, "Failure", "Find failures.", None, [], "model")
        client = JudgeLaminarSQL(
            "test-key", "http://laminar.test", transport=httpx.MockTransport(handler)
        )
        try:
            return await _prepare_judge_input(client, trace, version)
        finally:
            await client.aclose()

    async def test_rate_limits_and_server_errors_give_back_attempt_without_retrying(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                requests = []

                def handler(request):
                    requests.append(request)
                    return httpx.Response(status)

                with self.assertRaisesRegex(JudgeError, f"HTTP {status}") as caught:
                    await self.prepare(handler)

                self.assertTrue(caught.exception.retryable)
                self.assertFalse(caught.exception.uses_attempt)
                self.assertEqual(len(requests), 1)

    async def test_connection_failure_gives_back_attempt(self):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        with self.assertRaisesRegex(JudgeError, "Could not reach Laminar") as caught:
            await self.prepare(handler)

        self.assertTrue(caught.exception.retryable)
        self.assertFalse(caught.exception.uses_attempt)

    async def test_fetch_deadline_gives_back_attempt(self):
        async def handler(request):
            await asyncio.Event().wait()

        with patch.dict("os.environ", {"JUDGE_FETCH_TIMEOUT_SECONDS": "0.05"}):
            with self.assertRaisesRegex(JudgeError, "deadline expired") as caught:
                await self.prepare(handler)

        self.assertTrue(caught.exception.retryable)
        self.assertFalse(caught.exception.uses_attempt)

    async def test_bad_query_and_auth_errors_remain_laminar_errors(self):
        for status in (400, 401, 403):
            with self.subTest(status=status):
                def handler(request):
                    return httpx.Response(status, json={"error": "request rejected"})

                with self.assertRaisesRegex(LaminarError, f"HTTP {status}: request rejected") as caught:
                    await self.prepare(handler)

                self.assertNotIsInstance(caught.exception, LaminarUnavailable)
