"""Check evidence cited in structured judge output."""

from tripwire.judge.render import Rendered
from tripwire.judge.types import Finding


JUDGE_MAX_FINDINGS = 5
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def check_findings(
    raw_findings: list[dict], rendered: Rendered
) -> tuple[list[Finding], int]:
    """Resolve citations, count uncited findings, and cap the result by severity."""
    findings = []
    dropped_findings = 0
    for raw_finding in raw_findings:
        evidence_labels = [
            label for label in raw_finding["evidence"] if label in rendered.labels
        ]
        if not evidence_labels:
            dropped_findings += 1
            continue

        findings.append(
            Finding(
                severity=raw_finding["severity"],
                span_ids=[rendered.labels[label] for label in evidence_labels],
                fields=raw_finding["fields"],
                evidence_labels=evidence_labels,
            )
        )

    if len(findings) > JUDGE_MAX_FINDINGS:
        findings = sorted(
            findings, key=lambda finding: SEVERITY_RANK[finding.severity]
        )[:JUDGE_MAX_FINDINGS]

    return findings, dropped_findings
