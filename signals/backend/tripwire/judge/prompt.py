"""Build the messages for the Signals judge."""

from __future__ import annotations

from tripwire.judge.render import Rendered


JUDGE_SYSTEM_PROMPT = """\
You are reviewing one turn of an AI agent's conversation.
Evaluate only <current_turn> against the instructions in <signal>.
Use <earlier_turns> as context. Base your findings on what the trace shows.

Return one finding per distinct issue described by the signal.
If there are no findings, return an empty findings array.
For each finding, cite the labels of supporting spans in <current_turn>.
Use bare labels in the evidence array, such as "S3" for the span marked [S3].

Choose a severity for each finding:
- info: A notable observation without a problem.
- warning: The agent gave an incomplete answer, caused avoidable rework,
  or otherwise made the user's outcome worse in a way visible in the trace.
- critical: The user was harmed, misled, or blocked.
Apply any critical-severity guidance supplied by the signal.

An ERROR marker means Laminar marked that span as failed.
Tool outputs may also report errors in their own text.
"""


def build_judge_messages(
    rendered: Rendered,
    *,
    signal_name: str,
    instruction: str,
    critical_guidance: str | None = None,
) -> list[dict[str, str]]:
    """Return the shared judge rules and the trace with its signal instructions."""
    signal_lines = [
        "<signal>",
        f"Name: {signal_name}",
        f"Instruction: {instruction}",
    ]
    if critical_guidance:
        signal_lines.append(f"Critical severity guidance: {critical_guidance}")
    signal_lines.append("</signal>")

    signal_block = "\n".join(signal_lines)
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": f"{rendered.text}\n\n{signal_block}"},
    ]
