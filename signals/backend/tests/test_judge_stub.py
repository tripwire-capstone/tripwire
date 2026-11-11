from datetime import datetime, timezone

import pytest

from tripwire.judge import judge as judge_module
from tripwire.judge.judge import judge
from tripwire.judge.types import JudgeError, SignalVersion, TraceRow, Usage

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)
VERSION = SignalVersion(id=1, signal_id=1, name="Failure", instruction="Find failures.",
                        critical_guidance=None, output_schema=[], model="stub")


def trace(n):
    return TraceRow(id=n, laminar_trace_id=f"00000000-0000-0000-0000-{n:012d}", session_id=None,
                    started_at=NOW, ended_at=NOW, status="success")


def test_same_trace_same_answer():
    assert judge(None, trace(1), VERSION) == judge(None, trace(1), VERSION)


def test_zero_one_or_two_findings_across_traces():
    counts = {len(judge(None, trace(n), VERSION).findings) for n in range(200)}
    assert counts == {0, 1, 2}


def test_rate_zero_finds_nothing():
    assert judge(None, trace(1), VERSION, event_rate=0).findings == []


def test_stub_error_is_raised(monkeypatch):
    err = JudgeError("rate limited", retryable=True, uses_attempt=False, retry_after=7,
                     usage=Usage(input_tokens=100, cost_usd=0.001))
    monkeypatch.setattr(judge_module, "stub_error", err)
    with pytest.raises(JudgeError) as caught:
        judge(None, trace(1), VERSION)
    assert caught.value.retryable and not caught.value.uses_attempt
    assert caught.value.retry_after == 7
    assert caught.value.usage.input_tokens == 100


def test_usage_adds_up():
    total = Usage(100, 20, 50, 0.01) + Usage(200, 0, 80, 0.02)
    assert (total.input_tokens, total.cached_input_tokens, total.output_tokens) == (300, 20, 130)
    assert total.cost_usd == pytest.approx(0.03)


def test_unknown_cost_stays_unknown():
    assert (Usage(cost_usd=0.01) + Usage(cost_usd=None)).cost_usd is None


def test_judge_error_without_usage_defaults_to_zero():
    assert JudgeError("x", retryable=False, uses_attempt=False).usage == Usage()