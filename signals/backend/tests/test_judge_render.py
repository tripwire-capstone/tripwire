import json
import unittest
from pathlib import Path

from tripwire.judge.render import render_trace


FIXTURES = Path(__file__).parent / "fixtures" / "traces"
FIRST_TURN = "6f2dc2fb-aec1-0a44-3f08-44039c123a87"
MULTI_TURN = "1d56acce-5f8c-6083-06f0-288555864512"
# park-traffic: calls the OpenAI SDK directly, so its LLM spans use OpenAI's `choices` layout
OPENAI_TOOL_TURN = "6a9ad768-be52-6ec0-a717-39da134bcdc8"
OPENAI_LATER_TURN = "864a7724-40f7-9fa8-2f0b-f36588e77fb2"


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


def llm_span(output, status="success"):
    return {
        "span_id": "llm",
        "parent_span_id": "root",
        "started_utc": "2026-11-11 17:01:57.000000",
        "span_type": "LLM",
        "name": "openai.chat",
        "input": "[]",
        "output": json.dumps(output),
        "events": [],
        "status": status,
    }


def choice(message):
    return {"finish_reason": "stop", "index": 0, "logprobs": None, "message": message}


class OpenAILayoutTests(unittest.TestCase):
    """SIG-92: replies from a direct OpenAI SDK call were dropped from the judge's input."""

    def test_tool_calls_and_reply_from_direct_openai_call_reach_the_judge(self):
        trace = load_trace(OPENAI_TOOL_TURN)
        rendered = render_trace(trace["spans"], trace["earlier_turns"])
        self.assertIn("[S2] ASSISTANT TOOL CALL: check_campsite_availability(", rendered.text)
        self.assertIn("[S3] TOOL check_campsite_availability", rendered.text)
        self.assertIn("[S4] ASSISTANT TOOL CALL: book_campsite(", rendered.text)
        self.assertIn("[S5] TOOL book_campsite", rendered.text)
        self.assertIn(
            "[S6] ASSISTANT: Your campsite has been successfully booked at Acadia National Park!",
            rendered.text,
        )
        self.assertEqual(list(rendered.labels.values()), [s["span_id"] for s in trace["spans"]])

    def test_plain_reply_from_direct_openai_call_reaches_the_judge(self):
        trace = load_trace(OPENAI_LATER_TURN)
        rendered = render_trace(trace["spans"], trace["earlier_turns"])
        self.assertIn("[S2] ASSISTANT: You're very welcome!", rendered.text)
        self.assertEqual(len(rendered.labels), 2)

    def test_empty_reply_is_shown_not_dropped(self):
        for name, output in [
            ("litellm", [{"role": "assistant", "content": None, "tool_calls": None}]),
            ("openai", [choice({"role": "assistant", "content": "", "tool_calls": None})]),
            ("plain string", ""),
        ]:
            with self.subTest(name), self.assertNoLogs("judge", "ERROR"):
                rendered = render_trace([llm_span(output)], [])
                self.assertIn("[S1] ASSISTANT: (empty reply)", rendered.text)

    def test_missing_output_is_shown_and_keeps_the_error(self):
        span = dict(llm_span(None), status="error")
        with self.assertNoLogs("judge", "ERROR"):
            rendered = render_trace([span], [])
        self.assertIn("[S1] ASSISTANT: (no output)\nERROR", rendered.text)

    def test_unrecognized_layout_gives_judge_the_raw_output_and_logs_an_error(self):
        responses_api = [{"type": "message", "content": [{"type": "output_text", "text": "Hi"}]}]
        two_choices = [choice({"role": "assistant", "content": "A"}),
                       dict(choice({"role": "assistant", "content": "B"}), index=1)]
        for name, output, raw_text in [
            ("responses api", responses_api, '"text": "Hi"'),
            ("two choices", two_choices, '"content": "A"'),
            ("dict", {"text": "Hi"}, '"text": "Hi"'),
            ("empty list", [], "[]"),
            ("list of strings", ["Hi"], '["Hi"]'),
        ]:
            with self.subTest(name), self.assertLogs("judge", "ERROR") as logs:
                rendered = render_trace([llm_span(output)], [])
                self.assertIn("[S1] ASSISTANT (unrecognized output layout): ", rendered.text)
                self.assertIn(raw_text, rendered.text)
                self.assertIn("LLM span llm (openai.chat)", logs.output[0])
