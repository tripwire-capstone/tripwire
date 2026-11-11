import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call

from tripwire.judge.judge import _complete_judgment
from tripwire.judge.render import Rendered
from tripwire.judge.types import JudgeError, SignalVersion, Usage
from tripwire.llm import LLMBadOutput, LLMInputTooLong, LLMRefused, LLMServiceError, LLMUsage


class JudgeModelTests(unittest.TestCase):
    def setUp(self):
        self.rendered = Rendered("[S1] USER: Help", {"S1": "span-1"})
        self.messages = [{"role": "user", "content": self.rendered.text}]
        self.schema = {"type": "object"}
        self.version = SignalVersion(None, None, "Failure", "Find failures.", None, [], "model")
        self.initial_call = call(
            "model", self.messages, self.schema, timeout=60, max_output_tokens=4_000
        )
        self.repair_call = call(
            "model", self.messages, self.schema, timeout=60, max_output_tokens=8_000
        )

    def run_model(self, complete):
        return _complete_judgment(
            self.rendered, self.messages, self.schema, self.version, complete=complete
        )

    def test_cutoff_repairs_once_with_same_messages_and_adds_usage(self):
        cutoff = LLMBadOutput("cut off", finish_reason="length", usage=LLMUsage(8, 1, 4, 0.1))
        response = SimpleNamespace(
            parsed={"analysis": "No issue.", "findings": []},
            raw="{}",
            model="model",
            input_tokens=9,
            cached_input_tokens=2,
            output_tokens=5,
            cost_usd=0.2,
        )
        complete = Mock(side_effect=[cutoff, response])

        result = self.run_model(complete)

        self.assertEqual(complete.call_args_list, [self.initial_call, self.repair_call])
        self.assertIs(complete.call_args_list[0].args[1], self.messages)
        self.assertIs(complete.call_args_list[1].args[1], self.messages)
        self.assertEqual(result.findings, [])
        self.assertEqual(result.usage, Usage(17, 3, 9, 0.1 + 0.2))

    def test_bad_output_uses_an_attempt_and_only_cutoff_gets_a_repair(self):
        invalid = LLMBadOutput("invalid JSON", usage=LLMUsage(7, 1, 3, 0.1))
        cutoff = LLMBadOutput("cut off", finish_reason="length", usage=LLMUsage(8, 2, 4))
        for errors, calls, expected_usage in [
            ([invalid], [self.initial_call], Usage(7, 1, 3, 0.1)),
            ([cutoff, cutoff], [self.initial_call, self.repair_call], Usage(16, 4, 8, None)),
        ]:
            with self.subTest(calls=len(calls)):
                complete = Mock(side_effect=errors)
                with self.assertRaises(JudgeError) as caught:
                    self.run_model(complete)
                self.assertTrue(caught.exception.retryable)
                self.assertTrue(caught.exception.uses_attempt)
                self.assertEqual(caught.exception.usage, expected_usage)
                self.assertEqual(complete.call_args_list, calls)

    def test_rate_limit_leaves_retry_to_runner_and_preserves_prior_usage(self):
        cutoff = LLMBadOutput("cut off", finish_reason="length", usage=LLMUsage(8, 1, 4, 0.1))
        rate_limit = LLMServiceError("rate limited", retry_after=7)
        for errors, calls, expected_usage in [
            ([rate_limit], [self.initial_call], Usage()),
            ([cutoff, rate_limit], [self.initial_call, self.repair_call], Usage(8, 1, 4, 0.1)),
        ]:
            with self.subTest(calls=len(calls)):
                complete = Mock(side_effect=errors)
                with self.assertRaises(JudgeError) as caught:
                    self.run_model(complete)
                self.assertTrue(caught.exception.retryable)
                self.assertFalse(caught.exception.uses_attempt)
                self.assertEqual(caught.exception.retry_after, 7)
                self.assertEqual(caught.exception.usage, expected_usage)
                self.assertEqual(complete.call_args_list, calls)

    def test_refusal_and_oversized_input_end_the_run(self):
        for error, expected_usage in [
            (LLMRefused("refused"), Usage(cost_usd=None)),
            (LLMInputTooLong("too long"), Usage()),
        ]:
            with self.subTest(error=type(error).__name__):
                complete = Mock(side_effect=error)
                with self.assertRaises(JudgeError) as caught:
                    self.run_model(complete)
                self.assertFalse(caught.exception.retryable)
                self.assertEqual(caught.exception.rendered_trace, self.rendered.text)
                self.assertEqual(caught.exception.usage, expected_usage)
                self.assertEqual(complete.call_args_list, [self.initial_call])
