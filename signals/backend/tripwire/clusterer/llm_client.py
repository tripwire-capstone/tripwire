"""The summarizer's OpenAI client. Its error and usage types are the shared ones in `tripwire/llm/`.

Step 1 of moving onto the shared module: the types are shared, the request code is still ours. The
shared request code (`tripwire/llm/_openai.py`) is the judge's for now; using it here waits on a
public `complete()` with a schema-name parameter, a shared price table and an agreed rule for
401/403 (step 2, with the Signals pair).

One call to complete() = one request to OpenAI: the SDK's own automatic retries are off, because
the summarizer decides about retries itself (Q20) and every paid call must be counted.

Every failure becomes one of four errors, which is what the summarizer's rules are written against:
  LLMServiceError   timeout, rate limit, OpenAI down     -> wait, no attempt used
  LLMBadOutput      not valid JSON for the schema, cut off -> uses an attempt
  LLMInputTooLong   over the model's context window     -> fail at once
  LLMRefused        the model declined                  -> fail at once
Anything else (a wrong API key, a bad model name, a bug in our request) is NOT translated: it
raises as-is, so a setup mistake shows up loudly instead of looking like a model problem.
"""

import json
from dataclasses import dataclass, field

from ..llm import LLMBadOutput, LLMInputTooLong, LLMRefused, LLMServiceError, LLMUsage

# Prices per 1M tokens, for cost_usd. A model not listed gets cost None ("not priced"), never 0.
# gpt-6-luna: $0.10 input, $0.01 cached read, $0.50 output (checked Nov 8); cache writes cost
# 1.25x input on GPT-5.6 and later (OpenAI prompt caching guide, checked Nov 10).
PRICES = {
    "gpt-6-luna": {"input": 0.10, "cached": 0.01, "cache_write": 0.125, "output": 0.50},
}

REQUEST_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class LLMResult:
    output: dict
    usage: LLMUsage


def price(model, input_tokens, cached_tokens, cache_write_tokens, output_tokens):
    rates = PRICES.get(model)
    if rates is None:
        return None
    plain = input_tokens - cached_tokens - cache_write_tokens
    total = (plain * rates["input"] + cached_tokens * rates["cached"]
             + cache_write_tokens * rates["cache_write"] + output_tokens * rates["output"])
    return round(total / 1_000_000, 6)


class OpenAIClient:
    def __init__(self, api_key, http_client=None):
        import openai   # imported here so placeholder mode doesn't need it
        self._openai = openai
        # http_client: tests pass one with a fake transport, so no request leaves the machine.
        self._client = openai.OpenAI(api_key=api_key, max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS,
                                     http_client=http_client)

    def complete(self, model, messages, json_schema):
        openai = self._openai
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": "output", "strict": True, "schema": json_schema}},
            )
        except (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError,
                openai.InternalServerError) as error:
            raise LLMServiceError(f"{type(error).__name__}: {error}",
                                  retry_after=_retry_after(error)) from error
        except openai.BadRequestError as error:
            if getattr(error, "code", None) == "context_length_exceeded":
                raise LLMInputTooLong(str(error)) from error
            raise    # any other 400 is a mistake in our request: fail loudly

        usage = _usage(model, response.usage)
        choice = response.choices[0]
        if choice.message.refusal:
            raise LLMRefused(choice.message.refusal, usage=usage)
        if choice.finish_reason == "length":
            raise LLMBadOutput("answer was cut off (finish_reason=length)", usage=usage)
        try:
            output = json.loads(choice.message.content or "")
        except ValueError as error:
            raise LLMBadOutput(f"answer isn't valid JSON: {error}", usage=usage) from error
        return LLMResult(output=output, usage=usage)


def _usage(model, raw):
    if raw is None:
        return LLMUsage()
    details = raw.prompt_tokens_details
    cached = (details.cached_tokens or 0) if details else 0
    writes = (getattr(details, "cache_write_tokens", None) or 0) if details else 0
    return LLMUsage(
        input_tokens=raw.prompt_tokens,
        cached_input_tokens=cached,
        output_tokens=raw.completion_tokens,
        cost_usd=price(model, raw.prompt_tokens, cached, writes, raw.completion_tokens),
    )


def _retry_after(error):
    response = getattr(error, "response", None)
    if response is None:
        return None
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None    # an HTTP date instead of seconds: ignore it and use our own wait


@dataclass
class FakeLLM:
    """For tests: plays back a script of results and errors, and records every request.

    `script` items are dicts (returned as the output, with `usage`) or LLMError instances (raised).
    """
    script: list
    usage: LLMUsage = field(default_factory=lambda: LLMUsage(100, 0, 20, 0.00002))
    requests: list = field(default_factory=list)

    def complete(self, model, messages, json_schema):
        self.requests.append({"model": model, "messages": messages, "json_schema": json_schema})
        if not self.script:
            raise AssertionError("FakeLLM ran out of scripted answers")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(output=item, usage=self.usage)
