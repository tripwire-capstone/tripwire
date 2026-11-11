"""What goes into and comes out of judge(): the interface between the job-flow and judge halves.

Changing a field here changes both halves: do it in its own small PR that both Signals-pair
members approve (HANDOFF_JOB_FLOW.md, A2).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Severity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class TraceRow:
    """One row of our `traces` table: the turn to judge."""
    id: int
    laminar_trace_id: str
    session_id: str | None          # None when the app sends no session ID
    started_at: datetime
    ended_at: datetime
    status: Literal["success", "error"]


@dataclass(frozen=True)
class SignalVersion:
    """A saved signal version, or an unsaved draft for test-on-trace (id and signal_id None)."""
    id: int | None
    signal_id: int | None
    name: str
    instruction: str
    critical_guidance: str | None   # optional "what counts as critical" text
    output_schema: list[dict]       # the signal's fields: [{name, type, description, choices, nullable}]
    model: str


@dataclass(frozen=True)
class Usage:
    """Tokens and cost of every model call inside one judge(), the repair included."""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = 0.0    # None = at least one call's price is unknown

    def __add__(self, other: "Usage") -> "Usage":
        cost = None if self.cost_usd is None or other.cost_usd is None else self.cost_usd + other.cost_usd
        return Usage(self.input_tokens + other.input_tokens,
                     self.cached_input_tokens + other.cached_input_tokens,
                     self.output_tokens + other.output_tokens,
                     cost)


@dataclass(frozen=True)
class Finding:
    """One problem the judge found. Becomes one signal_events row."""
    severity: Severity
    span_ids: list[str]             # real Laminar span IDs (evidence)
    fields: dict                    # the signal's own fields -> signal_events.payload
    evidence_labels: list[str] = field(default_factory=list)   # the same evidence as labels in judge_input, e.g. ["S3"]


@dataclass(frozen=True)
class JudgeResult:
    findings: list[Finding]         # 0..5; empty = found nothing
    analysis: str                   # the judge's reasoning -> signal_runs.analysis
    raw_output: str                 # the model's raw answer -> signal_runs.raw_output
    judge_input: str                # the rendered trace -> each event's judge_input
    dropped_findings: int           # removed by the evidence check
    model: str
    usage: Usage


class JudgeError(Exception):
    """The run couldn't be judged. The runner decides what happens next from these flags.

    retryable=True,  uses_attempt=False  service problem or timeout: back off, attempt given back
    retryable=True,  uses_attempt=True   bad output (cut off twice): back off, uses an attempt
    retryable=False                      refusal / input too long: fail at once
    """

    def __init__(self, message: str, *, retryable: bool, uses_attempt: bool,
                 retry_after: float | None = None, rendered_trace: str | None = None,
                 usage: Usage | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.uses_attempt = uses_attempt
        self.retry_after = retry_after          # seconds, if the provider said how long to wait
        self.rendered_trace = rendered_trace    # stored on the run when it fails for good
        self.usage = usage or Usage()           # spent before failing; the runner still records it