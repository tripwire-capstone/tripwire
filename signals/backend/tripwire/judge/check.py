"""Check evidence cited in structured judge output."""

from tripwire.judge.render import Rendered
from tripwire.judge.types import Finding


def check_findings(
    raw_findings: list[dict], rendered: Rendered
) -> tuple[list[Finding], int]:
    """Resolve citations in schema-validated findings and count uncited findings."""
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

    return findings, dropped_findings
