"""Tests for the embedder: the fake's promises, the size checks, and the OpenAI client against a fake server."""

import httpx
import openai
import pytest

from tripwire.clusterer.embed import DIMENSIONS, FakeEmbedder, OpenAIEmbedder, check_vectors
from tripwire.clusterer.llm_client import LLMServiceError


def test_fake_embedder_same_text_same_vector_different_text_different_vector():
    embedder = FakeEmbedder()
    first, again, other = embedder.embed(["Refund fails.", "Refund fails.", "Login fails."])
    assert first == again and first != other
    assert len(first) == DIMENSIONS


def test_check_vectors_rejects_a_wrong_count_or_size():
    with pytest.raises(ValueError, match="asked for 2 embeddings, got 1"):
        check_vectors([[0.1] * DIMENSIONS], 2)
    with pytest.raises(ValueError, match="has 3 numbers"):
        check_vectors([[0.1, 0.2, 0.3]], 1)


def embedder_answering(status, body):
    def handler(request):
        return httpx.Response(status, json=body)
    return OpenAIEmbedder("test-key", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def embeddings_body(*vectors):
    # Returned out of order on purpose: the embedder must sort by `index`.
    data = [{"object": "embedding", "index": index, "embedding": vector} for index, vector in enumerate(vectors)]
    return {"object": "list", "data": list(reversed(data)), "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 10, "total_tokens": 10}}


def test_openai_embedder_returns_vectors_in_the_order_asked():
    a, b = [0.1] * DIMENSIONS, [0.2] * DIMENSIONS
    assert embedder_answering(200, embeddings_body(a, b)).embed(["a", "b"]) == [a, b]


def test_openai_embedder_turns_rate_limits_and_outages_into_service_errors():
    with pytest.raises(LLMServiceError):
        embedder_answering(429, {"error": {"message": "slow down", "type": "rate_limit"}}).embed(["a"])
    with pytest.raises(LLMServiceError):
        embedder_answering(503, {"error": {"message": "down", "type": "server_error"}}).embed(["a"])


def test_openai_embedder_lets_a_wrong_key_raise_as_is():
    with pytest.raises(openai.AuthenticationError):
        embedder_answering(401, {"error": {"message": "bad key", "type": "invalid_api_key"}}).embed(["a"])
