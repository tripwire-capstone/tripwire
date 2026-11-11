"""STUB judge: returns made-up results in the real shape, without Laminar or an LLM.

The judge half replaces the body of judge() (HANDOFF_JUDGE.md, B5). The signature and the
types never change, so the runner doesn't either.
"""

import asyncio
import math
import os

from ..llm import LLMBadOutput, LLMError, LLMInputTooLong, LLMRefused, LLMServiceError
from ..placeholder import picked
from .check import check_findings
from .fetch import fetch_earlier_turns_async, fetch_spans_async
from .laminar import LaminarUnavailable
from .prompt import build_judge_messages
from .render import Rendered, render_trace
from .schema import build_judge_schema
from .types import Finding, JudgeError, JudgeResult, SignalVersion, TraceRow, Usage

# Tests set this to make the next judge() call raise it (see tests/test_judge_stub.py).
stub_error: JudgeError | None = None


async def _prepare_judge_input(
    laminar, trace: TraceRow, version: SignalVersion
) -> tuple[Rendered, list[dict[str, str]], dict]:
    """Fetch and render the trace, then build the model messages and schema."""
    fetch_timeout = float(os.getenv("JUDGE_FETCH_TIMEOUT_SECONDS", "10"))
    if not math.isfinite(fetch_timeout) or fetch_timeout <= 0:
        raise ValueError("JUDGE_FETCH_TIMEOUT_SECONDS must be positive and finite")

    clock = asyncio.get_running_loop().time
    try:
        spans = await fetch_spans_async(
            laminar,
            trace.laminar_trace_id,
            trace.started_at,
            deadline=clock() + fetch_timeout,
        )
        earlier_turns = await fetch_earlier_turns_async(
            laminar,
            trace.session_id,
            trace.started_at,
            deadline=clock() + fetch_timeout,
        )
    except (TimeoutError, LaminarUnavailable) as exc:
        detail = str(exc) or "query deadline expired"
        raise JudgeError(
            f"Could not fetch the trace from Laminar: {detail}",
            retryable=True,
            uses_attempt=False,
        ) from exc

    rendered = render_trace(spans, earlier_turns)
    messages = build_judge_messages(
        rendered,
        signal_name=version.name,
        instruction=version.instruction,
        critical_guidance=version.critical_guidance,
    )
    schema = build_judge_schema(version.output_schema)
    return rendered, messages, schema


def _complete_judgment(
    rendered: Rendered,
    messages: list[dict[str, str]],
    schema: dict,
    version: SignalVersion,
    *,
    complete,
    model_timeout: float = 60,
) -> JudgeResult:
    """Check model output, allowing one repair if the response was cut off."""
    usage = Usage()
    for max_output_tokens in (4_000, 8_000):
        try:
            response = complete(
                version.model,
                messages,
                schema,
                timeout=model_timeout,
                max_output_tokens=max_output_tokens,
            )
        except LLMBadOutput as exc:
            usage += _error_usage(exc)
            if exc.finish_reason == "length" and max_output_tokens == 4_000:
                continue
            raise JudgeError(
                str(exc),
                retryable=True,
                uses_attempt=True,
                rendered_trace=rendered.text,
                usage=usage,
            ) from exc
        except (LLMServiceError, LLMRefused, LLMInputTooLong) as exc:
            usage += _error_usage(exc)
            raise JudgeError(
                str(exc),
                retryable=isinstance(exc, LLMServiceError),
                uses_attempt=False,
                retry_after=exc.retry_after if isinstance(exc, LLMServiceError) else None,
                rendered_trace=rendered.text,
                usage=usage,
            ) from exc
        break

    findings, dropped_findings = check_findings(
        response.parsed["findings"], rendered
    )

    return JudgeResult(
        findings=findings,
        analysis=response.parsed["analysis"],
        raw_output=response.raw,
        judge_input=rendered.text,
        dropped_findings=dropped_findings,
        model=response.model,
        usage=usage + Usage(
            input_tokens=response.input_tokens,
            cached_input_tokens=response.cached_input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
        ),
    )


def _error_usage(error: LLMError) -> Usage:
    usage = error.usage
    if usage is None:
        if isinstance(error, (LLMBadOutput, LLMRefused)):
            # The model replied, so its cost is unknown if no usage was reported.
            return Usage(cost_usd=None)
        return Usage()
    return Usage(
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
    )


def judge(laminar, trace: TraceRow, version: SignalVersion, *, event_rate: float = 0.5) -> JudgeResult:
    if stub_error is not None:
        raise stub_error

    findings = []
    # Same trace -> same answer every time, like the placeholder runner.
    if picked(f"event:{trace.laminar_trace_id}", event_rate):
        findings.append(Finding(severity="warning", span_ids=[], fields={}, evidence_labels=["S1"]))
        if picked(f"event2:{trace.laminar_trace_id}", event_rate):
            findings.append(Finding(severity="info", span_ids=[], fields={}, evidence_labels=["S2"]))

    return JudgeResult(
        findings=findings,
        analysis="stub judge: no analysis",
        raw_output="{}",
        judge_input=f"stub judge_input for trace {trace.laminar_trace_id}",
        dropped_findings=0,
        model="stub",
        usage=Usage(),
    )
