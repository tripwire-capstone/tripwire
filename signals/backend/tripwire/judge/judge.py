"""STUB judge: returns made-up results in the real shape, without Laminar or an LLM.

The judge half replaces the body of judge() (HANDOFF_JUDGE.md, B5). The signature and the
types never change, so the runner doesn't either.
"""

from ..placeholder import picked
from .types import Finding, JudgeError, JudgeResult, SignalVersion, TraceRow, Usage

# Tests set this to make the next judge() call raise it (see tests/test_judge_stub.py).
stub_error: JudgeError | None = None


def judge(laminar, trace: TraceRow, version: SignalVersion, *, event_rate: float = 0.5) -> JudgeResult:
    if stub_error is not None:
        raise stub_error

    findings = []
    # Same trace -> same answer every time, like the placeholder runner.
    if picked(f"event:{trace.laminar_trace_id}", event_rate):
        findings.append(Finding(severity="warning", span_ids=[], fields={}, evidence_labels=["S1"]))
        if picked(f"event2:{trace.laminar_trace_id}", event_rate):
            findings.append(Finding(severity="info", span_ids=[], fields={}, evidence_labels=["S2"]))

    return JudgeResult(
        findings=findings,
        analysis="stub judge: no analysis",
        raw_output="{}",
        judge_input=f"stub judge_input for trace {trace.laminar_trace_id}",
        dropped_findings=0,
        model="stub",
        usage=Usage(),
    )