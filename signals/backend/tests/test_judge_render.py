import json
import unittest
from pathlib import Path

from tripwire.judge.render import render_trace


FIXTURES = Path(__file__).parent / "fixtures" / "traces"
FIRST_TURN = "6f2dc2fb-aec1-0a44-3f08-44039c123a87"
MULTI_TURN = "1d56acce-5f8c-6083-06f0-288555864512"


def load_trace(trace_id):
    return json.loads((FIXTURES / f"{trace_id}.json").read_text(encoding="utf-8"))


class RenderTraceTests(unittest.TestCase):
    def test_first_turn_has_empty_context_and_labelled_user_and_reply(self):
        trace = load_trace(FIRST_TURN)
        rendered = render_trace(trace["spans"], trace["earlier_turns"])
        self.assertIn('<earlier_turns context_only="true">\n</earlier_turns>', rendered.text)
        self.assertIn("[S1] USER: Hi there! I want to cancel the bookshelves", rendered.text)
        self.assertIn("[S2] ASSISTANT: ", rendered.text)
        self.assertEqual(list(rendered.labels.values()), [s["span_id"] for s in trace["spans"]])

    def test_multi_turn_context_and_evidence_are_deterministic(self):
        trace = load_trace(MULTI_TURN)
        spans, earlier = trace["spans"], trace["earlier_turns"]
        rendered = render_trace(spans, earlier)
        self.assertEqual(rendered, render_trace(list(reversed(spans)), earlier))
        context = rendered.text.split('<earlier_turns context_only="true">', 1)[1]
        context = context.split("</earlier_turns>", 1)[0]
        self.assertEqual(context.count("\nUSER: "), 3)
        self.assertEqual(context.count("\nASSISTANT: "), 3)
        self.assertNotIn("[S", context)
        self.assertEqual(rendered.text.count("<system_prompt>"), 1)
        self.assertIn("[S1] USER: Yes, I confirm all three actions.", rendered.text)
        self.assertIn("[S2] ASSISTANT TOOL CALL: modify_pending_order_address(", rendered.text)
        self.assertIn("[S3] TOOL modify_pending_order_address", rendered.text)
        self.assertIn("[S10] ASSISTANT: ", rendered.text)
        self.assertEqual(rendered.labels["S3"], spans[2]["span_id"])
        self.assertEqual(len(rendered.labels), 10)

    def test_tool_error_annotation_depends_on_span_status(self):
        tool = {
            "span_id": "tool",
            "parent_span_id": "root",
            "started_utc": "2026-11-11 17:01:57.000000",
            "span_type": "TOOL",
            "name": "lookup",
            "input": '{"id": 1}',
            "output": '{"error": "not found"}',
            "events": [],
            "status": "success",
        }
        for status, events, expected in [
            ("success", [], None),
            ("error", [], "ERROR"),
            ("error", [[0, "exception", '{"exception.type":"LookupError",'
                        '"exception.message":"not found"}']], "ERROR LookupError: not found"),
        ]:
            with self.subTest(status=status, events=events):
                rendered = render_trace([dict(tool, status=status, events=events)], [])
                self.assertIn('Result: {"error": "not found"}', rendered.text)
                if expected:
                    self.assertIn(f"\n{expected}\n", rendered.text)
                else:
                    self.assertNotIn("\nERROR", rendered.text)
