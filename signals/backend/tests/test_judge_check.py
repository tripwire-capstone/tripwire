import unittest

from tripwire.judge.check import check_findings
from tripwire.judge.render import Rendered
from tripwire.judge.types import Finding


class CheckFindingsTests(unittest.TestCase):
    def test_keeps_valid_evidence_with_its_own_finding(self):
        rendered = Rendered(
            text="[S3] Lookup failed\n[S5] Refund rejected\n[S6] Refund confirmed",
            labels={"S3": "lookup-span", "S5": "refund-span", "S6": "reply-span"},
        )
        raw_findings = [
            {
                "severity": "warning",
                "evidence": ["S3", "S99"],
                "fields": {"issue": "Lookup failed"},
            },
            {
                "severity": "critical",
                "evidence": ["S5", "S6"],
                "fields": {"issue": "Refund falsely confirmed"},
            },
            {"severity": "info", "evidence": ["S99"], "fields": {"issue": "Uncited"}},
        ]

        findings, dropped_findings = check_findings(raw_findings, rendered)

        self.assertEqual(dropped_findings, 1)
        self.assertEqual(
            findings,
            [
                Finding(
                    severity="warning",
                    span_ids=["lookup-span"],
                    fields={"issue": "Lookup failed"},
                    evidence_labels=["S3"],
                ),
                Finding(
                    severity="critical",
                    span_ids=["refund-span", "reply-span"],
                    fields={"issue": "Refund falsely confirmed"},
                    evidence_labels=["S5", "S6"],
                ),
            ],
        )
