"""The embedder: summaries -> vectors in event_embeddings (prepare half, PR 3; Design Notes Q21).

Only `summary` is embedded (Q1), many summaries per call. There is no failure bookkeeping: a summary
whose embedding call fails stays `done` without a vector and is simply picked again next pass. A
one-sentence input can't be too long and an embedding can't be refused, so the only expected failure
is a service problem (LLMServiceError); anything else raises as-is, like the summarizer's client.
"""

import hashlib
import math

from ..placeholder import vector_text
from .llm_client import REQUEST_TIMEOUT_SECONDS, LLMServiceError

EMBEDDING_MODEL = "text-embedding-3-small"
DIMENSIONS = 1536       # event_embeddings.embedding is vector(1536)


class OpenAIEmbedder:
    model = EMBEDDING_MODEL

    def __init__(self, api_key, http_client=None):
        import openai
        self._openai = openai
        self._client = openai.OpenAI(api_key=api_key, max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS,
                                     http_client=http_client)

    def embed(self, texts):
        """One call for all `texts`; returns their vectors in the same order."""
        openai = self._openai
        try:
            response = self._client.embeddings.create(model=self.model, input=list(texts))
        except (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError,
                openai.InternalServerError) as error:
            raise LLMServiceError(f"{type(error).__name__}: {error}") from error
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        return check_vectors(vectors, len(texts))


def check_vectors(vectors, expected_count):
    """Fail loudly on a wrong count or size instead of saving something the table would reject."""
    if len(vectors) != expected_count:
        raise ValueError(f"asked for {expected_count} embeddings, got {len(vectors)}")
    for vector in vectors:
        if len(vector) != DIMENSIONS:
            raise ValueError(f"embedding has {len(vector)} numbers, the table needs {DIMENSIONS}")
    return vectors


class FakeEmbedder:
    """For tests: the same text always gets the same 1536 numbers; different texts differ.
    Records every call, so tests can check that a batch went out as one call."""
    model = "fake-embedding"

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with      # an exception to raise instead of answering

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail_with is not None:
            raise self.fail_with
        return [_fake_vector(text) for text in texts]


def _fake_vector(text):
    seed = hashlib.sha256(text.encode()).digest()
    raw = [((seed[i % 32] + i * 7) % 251) / 251 + 0.01 for i in range(DIMENSIONS)]
    norm = math.sqrt(sum(value * value for value in raw))
    return [value / norm for value in raw]


def save_embedding(conn, event_id, vector, model):
    conn.execute("INSERT INTO event_embeddings (event_id, model, embedding) VALUES (%s, %s, %s)",
                 (event_id, model, vector_text(vector)))
