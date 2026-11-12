"""Shared error and usage types for one LLM request."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class LLMError(Exception):
    """An LLM failure with any usage reported by the provider.

    Use LLMUsage(cost_usd=None) when the request may have been billed and its
    cost could not be determined.
    """

    def __init__(self, message: str, *, usage: LLMUsage | None = None):
        super().__init__(message)
        self.usage = usage


class LLMServiceError(LLMError):
    """A service failure the caller can retry later."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        usage: LLMUsage | None = None,
    ):
        super().__init__(message, usage=usage)
        self.retry_after = retry_after


class LLMBadOutput(LLMError):
    """The model returned incomplete or invalid structured output."""

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str | None = None,
        usage: LLMUsage | None = None,
    ):
        super().__init__(message, usage=usage)
        self.finish_reason = finish_reason


class LLMInputTooLong(LLMError):
    """The request exceeded the model's context window."""


class LLMRefused(LLMError):
    """The model refused the request."""
