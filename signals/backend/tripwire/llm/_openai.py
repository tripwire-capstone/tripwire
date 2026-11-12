"""Make and parse a single structured-output request to OpenAI."""

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from httpx import Headers
from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai.types.responses import Response

from . import LLMBadOutput, LLMError, LLMInputTooLong, LLMRefused, LLMServiceError, LLMUsage


@dataclass(frozen=True)
class _OpenAIResult:
    parsed: dict
    raw: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float | None


def _complete_openai(
    model: str,
    messages: list[dict[str, str]],
    schema: dict,
    *,
    timeout: float,
    max_output_tokens: int,
) -> _OpenAIResult:
    response = asyncio.run(
        _request_openai(
            model,
            messages,
            schema,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
        )
    )
    parsed, usage = _parse_openai_response(response)

    return _OpenAIResult(
        parsed=parsed,
        raw=response.output_text,
        model=response.model,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
    )


async def _request_openai(
    model: str,
    messages: list[dict[str, str]],
    schema: dict,
    *,
    timeout: float,
    max_output_tokens: int,
) -> Response:
    try:
        async with AsyncOpenAI(max_retries=0, timeout=timeout) as client:
            async with asyncio.timeout(timeout):
                return await client.responses.create(
                    model=model,
                    input=messages,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "signal_judgment",
                            "strict": True,
                            "schema": schema,
                        },
                    },
                    max_output_tokens=max_output_tokens,
                    store=False,
                )
    except (TimeoutError, APIConnectionError) as exc:
        raise LLMServiceError(
            "OpenAI request timed out or the connection failed",
            usage=LLMUsage(),
        ) from exc
    except APIStatusError as exc:
        status = exc.status_code
        if status == 400 and exc.code == "context_length_exceeded":
            raise LLMInputTooLong("Trace exceeds the model's context window") from exc

        server_error = 500 <= status < 600
        if status in {401, 403, 429} or server_error:
            raise LLMServiceError(
                f"OpenAI request failed (HTTP {status})",
                retry_after=_retry_after_seconds(exc.response.headers),
                usage=LLMUsage() if server_error else None,
            ) from exc
        raise


def _retry_after_seconds(headers: Headers) -> float | None:
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1)):
        try:
            seconds = float(headers.get(name, "")) * scale
        except ValueError:
            continue
        if math.isfinite(seconds) and seconds >= 0:
            return seconds

    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        retry_at = parsedate_to_datetime(value)
    except (ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _parse_openai_response(response: Response) -> tuple[dict, LLMUsage]:
    usage = LLMUsage()
    if response.usage is not None:
        tokens = response.usage
        details = tokens.input_tokens_details
        usage = LLMUsage(
            input_tokens=tokens.input_tokens,
            cached_input_tokens=details.cached_tokens if details else 0,
            output_tokens=tokens.output_tokens,
        )

    for item in response.output:
        if item.type == "message":
            for part in item.content:
                if part.type == "refusal":
                    raise LLMRefused("OpenAI refused the request", usage=usage)

    if response.status == "incomplete":
        details = response.incomplete_details
        reason = details.reason if details else None
        raise LLMBadOutput(
            f"OpenAI returned an incomplete response ({reason})",
            finish_reason="length" if reason == "max_output_tokens" else None,
            usage=usage,
        )

    if response.status != "completed":
        error = response.error
        if error and error.code in {"server_error", "rate_limit_exceeded"}:
            raise LLMServiceError(error.message, usage=usage)
        detail = error.message if error else response.status
        raise LLMError(f"OpenAI response failed ({detail})", usage=usage)

    try:
        parsed = json.loads(response.output_text)
    except json.JSONDecodeError as exc:
        raise LLMBadOutput("OpenAI returned invalid JSON", usage=usage) from exc

    if not isinstance(parsed, dict):
        raise LLMBadOutput("Expected a JSON object", usage=usage)

    return parsed, usage
