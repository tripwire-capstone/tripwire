from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from tripwire.judge.judge import judge
from tripwire.judge.types import Finding, JudgeError, SignalVersion, TraceRow, Usage
from tripwire.llm._openai import _complete_openai


def test_judge_uses_real_adapter_and_configured_timeout(monkeypatch):
    now = datetime(2026, 11, 11, tzinfo=timezone.utc)
    trace = TraceRow(1, "00000000-0000-0000-0000-000000000001", None, now, now, "success")
    version = SignalVersion(1, 1, "Failure", "Find failures.", None, [], "test-model")
    laminar = Mock()
    monkeypatch.setenv("JUDGE_MODEL_TIMEOUT_SECONDS", "45")

    with patch("tripwire.judge.judge._judge_trace") as judge_trace:
        assert judge(laminar, trace, version) is judge_trace.return_value

    judge_trace.assert_called_once_with(
        laminar, trace, version, complete=_complete_openai, model_timeout=45
    )


def test_usage_adds_up():
    total = Usage(100, 20, 50, 0.01) + Usage(200, 0, 80, 0.02)
    assert (total.input_tokens, total.cached_input_tokens, total.output_tokens) == (300, 20, 130)
    assert total.cost_usd == pytest.approx(0.03)


def test_unknown_cost_stays_unknown():
    assert (Usage(cost_usd=0.01) + Usage(cost_usd=None)).cost_usd is None


def test_judge_error_without_usage_defaults_to_zero():
    assert JudgeError("x", retryable=False, uses_attempt=False).usage == Usage()


def test_finding_without_labels_defaults_to_empty():
    assert Finding(severity="info", span_ids=[], fields={}).evidence_labels == []
