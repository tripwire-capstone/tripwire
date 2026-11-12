"""Configuration shared by callers that select a judge model."""

import os


class JudgeModelConfigError(RuntimeError):
    """The deployment has no usable default judge model."""


def configured_judge_model() -> str:
    model = os.getenv("JUDGE_DEFAULT_MODEL", "").strip()
    if not model or model in {"stub", "placeholder"}:
        raise JudgeModelConfigError(
            "Set JUDGE_DEFAULT_MODEL to a usable OpenAI model ID."
        )
    return model
