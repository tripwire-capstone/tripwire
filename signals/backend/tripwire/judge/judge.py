"""Judge a finished Laminar trace using a saved or draft signal version."""

import asyncio
import math
import os

from ..llm import LLMBadOutput, LLMError, LLMInputTooLong, LLMRefused, LLMServiceError
from ..llm._openai import _complete_openai
from .check import check_findings
from .fetch import fetch_earlier_turns_async, fetch_spans_async
from .laminar import JudgeLaminarSQL, LaminarUnavailable
from .prompt import build_judge_messages
from .render import Rendered, render_trace
from .schema import build_judge_schema
from .types import JudgeError, JudgeResult, SignalVersion, TraceRow, Usage


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


def _judge_trace(
    laminar: JudgeLaminarSQL,
    trace: TraceRow,
    version: SignalVersion,
    *,
    complete,
    model_timeout: float,
) -> JudgeResult:
    """Judge one trace, taking ownership of a fresh Laminar client for the fetch."""
    async def prepare():
        try:
            return await _prepare_judge_input(laminar, trace, version)
        finally:
            await laminar.aclose()

    rendered, messages, schema = asyncio.run(prepare())
    return _complete_judgment(
        rendered,
        messages,
        schema,
        version,
        complete=complete,
        model_timeout=model_timeout,
    )


def judge(laminar: JudgeLaminarSQL, trace: TraceRow, version: SignalVersion) -> JudgeResult:
    return _judge_trace(
        laminar,
        trace,
        version,
        complete=_complete_openai,
        model_timeout=int(os.getenv("JUDGE_MODEL_TIMEOUT_SECONDS", "60")),
    )
