"""Tests for the OpenAI client's translation of responses and errors, against a fake HTTP server."""

import json

import httpx
import openai
import pytest

from tripwire.clusterer.llm_client import OpenAIClient
from tripwire.llm import LLMBadOutput, LLMInputTooLong, LLMRefused, LLMServiceError

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"], "additionalProperties": False}
USAGE = {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050,
         "prompt_tokens_details": {"cached_tokens": 200}}


def client_answering(status, body, headers=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(status, json=body, headers=headers or {})
    return OpenAIClient("test-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def completion(content=None, refusal=None, finish_reason="stop"):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "gpt-6-luna",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content, "refusal": refusal}}],
            "usage": USAGE}


def call(client):
    return client.complete("gpt-6-luna", [{"role": "user", "content": "hi"}], SCHEMA)


def test_good_answer_returns_parsed_output_usage_and_cost():
    seen = []
    result = call(client_answering(200, completion(json.dumps({"summary": "Fine."})), seen=seen))
    assert result.output == {"summary": "Fine."}
    assert (result.usage.input_tokens, result.usage.cached_input_tokens, result.usage.output_tokens) == (1000, 200, 50)
    # 800 plain x $0.10 + 200 cached x $0.01 + 50 out x $0.50, per 1M
    assert result.usage.cost_usd == pytest.approx((800 * 0.10 + 200 * 0.01 + 50 * 0.50) / 1e6)
    sent = seen[0]["response_format"]
    assert sent["type"] == "json_schema" and sent["json_schema"]["strict"] is True


def test_refusal_becomes_llm_refused_with_its_usage():
    with pytest.raises(LLMRefused) as raised:
        call(client_answering(200, completion(refusal="I can't help with that")))
    assert raised.value.usage.input_tokens == 1000


def test_cut_off_answer_is_bad_output():
    with pytest.raises(LLMBadOutput, match="cut off"):
        call(client_answering(200, completion('{"summ', finish_reason="length")))


def test_answer_that_isnt_json_is_bad_output():
    with pytest.raises(LLMBadOutput, match="valid JSON"):
        call(client_answering(200, completion("not json")))


def test_rate_limit_is_a_service_error_with_openais_wait_hint():
    with pytest.raises(LLMServiceError) as raised:
        call(client_answering(429, {"error": {"message": "slow down", "type": "rate_limit"}},
                              headers={"retry-after": "7"}))
    assert raised.value.retry_after == 7


def test_server_error_is_a_service_error():
    with pytest.raises(LLMServiceError):
        call(client_answering(503, {"error": {"message": "down", "type": "server_error"}}))


def test_context_length_exceeded_is_input_too_long():
    body = {"error": {"message": "too long", "type": "invalid_request_error", "code": "context_length_exceeded"}}
    with pytest.raises(LLMInputTooLong):
        call(client_answering(400, body))


def test_other_bad_requests_and_a_wrong_key_raise_as_is():
    # Our mistakes, not the model's: they must not look like bad output or an outage.
    with pytest.raises(openai.BadRequestError):
        call(client_answering(400, {"error": {"message": "bad schema", "type": "invalid_request_error"}}))
    with pytest.raises(openai.AuthenticationError):
        call(client_answering(401, {"error": {"message": "bad key", "type": "invalid_api_key"}}))
