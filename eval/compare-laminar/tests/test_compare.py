"""Unit tests for compare.py's pure logic — no live DB or Laminar project needed.

Run from eval/compare-laminar/: .venv/bin/python -m pytest tests/
(pytest runs unittest.TestCase classes natively, same convention as signals/backend/tests/.)

Deliberately NOT covered here: snapshot_tripwire()/snapshot_laminar() themselves (they need a
live DB/project — that's what the 2026-11-15 end-to-end smoke test against the dev DB was for,
not a unit test's job). What's covered is everything these functions hand off to: the agreement
categorization, the cluster-chain walking, and the ground-truth dimension building — exactly
the kind of logic where the uuid.UUID-vs-string bug (caught 2026-11-15 by actually running this
against a real trace, not by inspection) could silently reappear if someone edits this code
without a human staring at a live result.
"""

import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import compare  # noqa: E402


class PickTauDomainTests(unittest.TestCase):
    """Asked interactively in the wizard, before any setup — not left for someone to find only
    via --traffic-args after they've already set up a project for the wrong domain."""

    def test_leaves_an_explicit_cli_domain_alone_without_prompting(self):
        with mock.patch("builtins.input") as prompt:
            result = compare._pick_tau_domain("--domain airline")
        prompt.assert_not_called()
        self.assertEqual(result, "--domain airline")

    def test_enter_with_no_input_keeps_the_default_implicit(self):
        with mock.patch("builtins.input", return_value=""):
            result = compare._pick_tau_domain("")
        self.assertEqual(result, "")  # tau-traffic's own DEFAULT_DOMAIN (retail) applies

    def test_typing_a_valid_domain_appends_it(self):
        with mock.patch("builtins.input", return_value="telecom"):
            result = compare._pick_tau_domain("")
        self.assertEqual(result, "--domain telecom")

    def test_typing_a_valid_domain_appends_after_other_existing_args(self):
        with mock.patch("builtins.input", return_value="telecom"):
            result = compare._pick_tau_domain("--workers 2")
        self.assertEqual(result, "--workers 2 --domain telecom")

    def test_typo_exits_rather_than_silently_passing_it_through(self):
        with mock.patch("builtins.input", return_value="retial"):
            with self.assertRaises(SystemExit):
                compare._pick_tau_domain("")


class PickTauModelTests(unittest.TestCase):
    def test_leaves_an_explicit_cli_value_alone_without_prompting(self):
        with mock.patch("builtins.input") as prompt:
            result = compare._pick_tau_model("gpt-4.1", "Agent model", show_notes=True)
        prompt.assert_not_called()
        self.assertEqual(result, "gpt-4.1")

    def test_enter_with_no_input_returns_none_so_the_tools_own_default_applies(self):
        with mock.patch("builtins.input", return_value=""):
            result = compare._pick_tau_model(None, "Agent model", show_notes=True)
        self.assertIsNone(result)

    def test_typing_any_name_is_accepted_not_validated(self):
        """Unlike domain, model isn't a closed set — tau-traffic accepts any name LiteLLM
        understands via its own "Other" choice, so this must not reject unlisted names."""
        with mock.patch("builtins.input", return_value="claude-opus-5"):
            result = compare._pick_tau_model(None, "Agent model", show_notes=True)
        self.assertEqual(result, "claude-opus-5")


class ParseSignalMarkdownTests(unittest.TestCase):
    """_extract_blockquote / _extract_table: parse signals/*.md directly rather than keeping a
    second copy of the instruction/schema that could drift from what a human reads and copies
    into Laminar's dashboard by hand. Caught live 2026-11-16: a naive version treated the
    file's own hard-wrapping (long lines broken for readability) as real newlines, which would
    have silently inserted line breaks mid-sentence into every multi-line paragraph."""

    SAMPLE = (
        "# Sample\n\n"
        "**Instruction:**\n\n"
        "> Intro paragraph that got hard-wrapped at some point for\n"
        "> file readability, not because of a real line break.\n"
        ">\n"
        "> 1. First numbered point, also wrapped across more than one\n"
        ">    physical line in the file.\n"
        "> 2. Second point.\n\n"
        "**Output schema:**\n\n"
        "| field | type | nullable | description |\n"
        "|---|---|---|---|\n"
        "| `a` | string | no | plain string field |\n"
        "| `b` | enum (`x`/`y`/`z`) | yes | an enum field |\n"
    )

    def _write(self, text):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        f.write(text)
        f.close()
        return Path(f.name)

    def test_wrapped_lines_join_with_a_space_not_a_newline(self):
        instruction = compare._extract_blockquote(self.SAMPLE, "**Instruction:**")
        self.assertIn(
            "Intro paragraph that got hard-wrapped at some point for file readability, "
            "not because of a real line break.",
            instruction,
        )

    def test_numbered_points_stay_on_their_own_line_even_when_wrapped(self):
        instruction = compare._extract_blockquote(self.SAMPLE, "**Instruction:**")
        lines = instruction.splitlines()
        self.assertIn("1. First numbered point, also wrapped across more than one physical "
                      "line in the file.", lines)
        self.assertIn("2. Second point.", lines)

    def test_blank_quoted_line_is_a_real_paragraph_break(self):
        instruction = compare._extract_blockquote(self.SAMPLE, "**Instruction:**")
        self.assertIn("", instruction.splitlines())

    def test_table_parses_plain_and_enum_types_and_nullable(self):
        schema = compare._extract_table(self.SAMPLE, "**Output schema:**")
        self.assertEqual(schema, [
            {"name": "a", "type": "string", "nullable": False, "description": "plain string field"},
            {"name": "b", "type": "enum", "choices": ["x", "y", "z"], "nullable": True,
             "description": "an enum field"},
        ])

    def test_parse_signal_md_combines_both(self):
        path = self._write(self.SAMPLE)
        instruction, schema = compare._parse_signal_md(path)
        self.assertIn("1. First numbered point", instruction)
        self.assertEqual(len(schema), 2)
        path.unlink()

    def test_signal_md_for_matches_the_existing_filename_convention(self):
        self.assertEqual(compare._signal_md_for("User frustration").name, "user_frustration.md")
        self.assertEqual(compare._signal_md_for("Intent").name, "intent.md")
        self.assertEqual(compare._signal_md_for("Issues").name, "issues.md")


class SendTrafficTests(unittest.TestCase):
    """send_traffic() itself shells out for real (not run live here) — what's covered is the
    command it builds, including --traffic-args: appended last, so it can override a default
    (e.g. --mix) since both tools' argparse takes the last occurrence of a repeated flag."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {
            "PARK_TRAFFIC_DIR": "/fake/park-traffic", "TAU_TRAFFIC_DIR": "../fake-tau-bench",
        })
        self._env.start()
        self.addCleanup(self._env.stop)

    def _args(self, **overrides):
        base = dict(traffic="tau", n=10, label="x", seed=1, traffic_args="")
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_tau_command_includes_n_and_yes(self):
        with mock.patch.object(compare.subprocess, "run") as run:
            compare.send_traffic(self._args(traffic="tau", n=7))
        cmd = run.call_args.args[0]
        self.assertIn("-n", cmd)
        self.assertEqual(cmd[cmd.index("-n") + 1], "7")
        self.assertIn("--yes", cmd)

    def test_park_command_includes_n_mix_bugs_seed_label(self):
        with mock.patch.object(compare.subprocess, "run") as run:
            compare.send_traffic(self._args(traffic="park", n=5, label="my-run", seed=2))
        cmd = run.call_args.args[0]
        self.assertIn("--mix", cmd)
        self.assertIn("--bugs", cmd)
        self.assertEqual(cmd[cmd.index("--label") + 1], "my-run")
        self.assertEqual(cmd[cmd.index("--seed") + 1], "2")

    def test_traffic_args_are_appended(self):
        with mock.patch.object(compare.subprocess, "run") as run:
            compare.send_traffic(self._args(traffic="tau", traffic_args="--domain retail"))
        cmd = run.call_args.args[0]
        self.assertIn("--domain", cmd)
        self.assertEqual(cmd[cmd.index("--domain") + 1], "retail")

    def test_traffic_args_override_a_default_by_coming_last(self):
        with mock.patch.object(compare.subprocess, "run") as run:
            compare.send_traffic(self._args(traffic="park", traffic_args="--mix forgetting"))
        cmd = run.call_args.args[0]
        # both "baseline" (the default) and "forgetting" (the override) are present; what
        # matters is "forgetting" comes after "baseline" so the target tool's own argparse
        # (last occurrence wins) picks it.
        self.assertLess(cmd.index("baseline"), cmd.index("forgetting"))


class EnvUpdateTests(unittest.TestCase):
    """_propose_env_update: caught live 2026-11-16 updating a commented historical line
    ("# previous key, for reference") and silently leaving the real active line — and
    therefore the key actually in effect — untouched."""

    def _write(self, text):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False)
        f.write(text)
        f.close()
        return Path(f.name)

    def test_updates_the_active_line_not_a_commented_duplicate_above_it(self):
        path = self._write(
            "# Original API key\n"
            "# LMNR_PROJECT_API_KEY=old-historical-value\n"
            "LMNR_PROJECT_API_KEY=old-active-value\n"
            "LMNR_API_URL=https://api.lmnr.ai\n"
        )
        new_text, changes = compare._propose_env_update(path, {"LMNR_PROJECT_API_KEY": "new-value"})
        self.assertEqual(changes, [("LMNR_PROJECT_API_KEY", "old-active-value", "new-value")])
        self.assertEqual(new_text.count("LMNR_PROJECT_API_KEY="), 2)  # the comment + the active line
        self.assertIn("# LMNR_PROJECT_API_KEY=old-historical-value", new_text)  # untouched
        self.assertIn("LMNR_PROJECT_API_KEY=new-value", new_text)
        self.assertNotIn("old-active-value", new_text)  # the old active value is gone, not duplicated
        path.unlink()

    def test_updates_the_active_line_when_it_comes_before_the_comment(self):
        """Same bug, opposite line order — must not depend on which one appears first."""
        path = self._write(
            "LMNR_PROJECT_API_KEY=old-active-value\n"
            "# LMNR_PROJECT_API_KEY=old-historical-value\n"
        )
        _, changes = compare._propose_env_update(path, {"LMNR_PROJECT_API_KEY": "new-value"})
        self.assertEqual(changes, [("LMNR_PROJECT_API_KEY", "old-active-value", "new-value")])
        path.unlink()

    def test_uncomments_when_only_a_commented_line_exists(self):
        path = self._write("# LMNR_PROJECT_ID=\n")
        new_text, changes = compare._propose_env_update(path, {"LMNR_PROJECT_ID": "abc-123"})
        self.assertEqual(changes, [("LMNR_PROJECT_ID", "", "abc-123")])
        self.assertIn("LMNR_PROJECT_ID=abc-123", new_text)
        path.unlink()

    def test_appends_when_key_is_entirely_missing(self):
        path = self._write("SOME_OTHER_KEY=x\n")
        new_text, changes = compare._propose_env_update(path, {"LMNR_PROJECT_ID": "abc-123"})
        self.assertEqual(changes, [("LMNR_PROJECT_ID", None, "abc-123")])
        self.assertIn("LMNR_PROJECT_ID=abc-123", new_text)
        path.unlink()

    def test_no_change_reported_when_value_already_matches(self):
        path = self._write("LMNR_PROJECT_API_KEY=already-this\n")
        _, changes = compare._propose_env_update(path, {"LMNR_PROJECT_API_KEY": "already-this"})
        self.assertEqual(changes, [])
        path.unlink()


class TripwireChainTests(unittest.TestCase):
    def test_single_level_chain(self):
        clusters_by_id = {1: {"id": 1, "name": "leaf", "level": 0, "parent_id": None}}
        self.assertEqual(
            compare.tripwire_chain(1, clusters_by_id),
            [{"id": 1, "name": "leaf", "level": 0}],
        )

    def test_multi_level_chain_walks_parent_id_to_the_root(self):
        """Today Tripwire only ever has level 0; this is what should happen with no code
        changes once levels 1-2 land (handoffs/cluster-trees-exploration.md)."""
        clusters_by_id = {
            1: {"id": 1, "name": "leaf", "level": 0, "parent_id": 2},
            2: {"id": 2, "name": "mid", "level": 1, "parent_id": 3},
            3: {"id": 3, "name": "root", "level": 2, "parent_id": None},
        }
        self.assertEqual(
            compare.tripwire_chain(1, clusters_by_id),
            [
                {"id": 1, "name": "leaf", "level": 0},
                {"id": 2, "name": "mid", "level": 1},
                {"id": 3, "name": "root", "level": 2},
            ],
        )

    def test_unknown_cluster_id_returns_empty_chain(self):
        self.assertEqual(compare.tripwire_chain(999, {}), [])


class ExtractReplyTextsTests(unittest.TestCase):
    """The two known LLM-output shapes — see the judge-render bug writeup for why they differ."""

    def test_flat_litellm_shape(self):
        output = '[{"content": "hello there", "role": "assistant", "tool_calls": null}]'
        self.assertEqual(compare._extract_reply_texts(output), ["hello there"])

    def test_nested_raw_openai_shape(self):
        output = ('[{"finish_reason": "stop", "index": 0, '
                   '"message": {"content": "hi from nested", "role": "assistant"}}]')
        self.assertEqual(compare._extract_reply_texts(output), ["hi from nested"])

    def test_tool_call_with_no_text_content_yields_nothing(self):
        output = ('[{"content": null, "role": "assistant", '
                   '"tool_calls": [{"function": {"name": "f", "arguments": "{}"}}]}]')
        self.assertEqual(compare._extract_reply_texts(output), [])

    def test_garbage_input_is_handled_quietly(self):
        self.assertEqual(compare._extract_reply_texts("not json"), [])
        self.assertEqual(compare._extract_reply_texts('{"not": "a list"}'), [])


class LooksLikeJsonTests(unittest.TestCase):
    def test_object_and_array_are_json(self):
        self.assertTrue(compare._looks_like_json('{"a": 1}'))
        self.assertTrue(compare._looks_like_json("[1, 2, 3]"))

    def test_natural_language_is_not_json(self):
        self.assertFalse(compare._looks_like_json("Here are the trail statuses..."))

    def test_empty_and_malformed_are_not_json(self):
        self.assertFalse(compare._looks_like_json(""))
        self.assertFalse(compare._looks_like_json("   "))
        self.assertFalse(compare._looks_like_json("{not valid"))


class GroundTruthDimensionsTests(unittest.TestCase):
    UNIVERSE = {"traceA", "traceB", "traceC"}
    SESSION_IDS = {"traceA": "sessA", "traceB": "sessB"}  # traceC: no session_id on either side

    def test_park_traffic_uses_planted_bug(self):
        snapshot = {"traffic": "park",
                    "answer_key": [{"convo_id": "sessA", "target_bug": "timeout"}]}
        dims = compare._ground_truth_dimensions(snapshot, self.UNIVERSE, self.SESSION_IDS)
        self.assertEqual(dims, {"planted bug": {
            "traceA": "timeout", "traceB": "(no planted bug)", "traceC": "(no planted bug)",
        }})

    def test_park_traffic_with_no_answer_key_yields_no_dimensions(self):
        self.assertEqual(
            compare._ground_truth_dimensions({"traffic": "park"}, self.UNIVERSE, self.SESSION_IDS),
            {},
        )

    def test_tau_bench_uses_pass_fail_and_json_quirk_independently(self):
        snapshot = {
            "traffic": "tau",
            "tau_results": [
                {"session_id": "sessA", "passed": True},
                {"session_id": "sessB", "passed": False},
            ],
            "laminar": {"llm_reply_texts": {
                "traceA": ["a normal reply"],
                "traceB": ['{"weird": "json"}'],
            }},
        }
        dims = compare._ground_truth_dimensions(snapshot, self.UNIVERSE, self.SESSION_IDS)
        self.assertEqual(dims["pass/fail"],
                          {"traceA": "passed", "traceB": "failed", "traceC": "(no result)"})
        self.assertEqual(dims["raw JSON reply"],
                          {"traceA": "no", "traceB": "yes", "traceC": "no"})

    def test_tau_bench_dimensions_are_independent_not_combined(self):
        """A trace can fail *and* reply in raw JSON, or fail with a perfectly normal reply —
        these must stay two separate tables, not collapsed into one."""
        snapshot = {
            "traffic": "tau",
            "tau_results": [{"session_id": "sessA", "passed": False}],
            "laminar": {"llm_reply_texts": {"traceA": ['{"still": "json"}']}},
        }
        dims = compare._ground_truth_dimensions(snapshot, {"traceA"}, {"traceA": "sessA"})
        self.assertEqual(dims["pass/fail"]["traceA"], "failed")
        self.assertEqual(dims["raw JSON reply"]["traceA"], "yes")


class CompareAgreementTests(unittest.TestCase):
    """Builds a full synthetic snapshot and checks compare()'s four-way agreement bucketing —
    the exact kind of cross-system matching that silently broke once already (trace ids
    compared as uuid.UUID vs str never matched, so every trace looked like "laminar_only" and
    "tripwire_only" at once). These use plain strings throughout, matching what
    snapshot_tripwire() is responsible for producing after its own cast."""

    def setUp(self):
        self._orig_pairs = compare.SIGNAL_PAIRS
        compare.SIGNAL_PAIRS = [("Issues", "Issues")]

    def tearDown(self):
        compare.SIGNAL_PAIRS = self._orig_pairs

    def _snapshot(self, **overrides):
        base = {
            "label": "test",
            "traffic": "park",
            "taken_at": "2026-11-16T00:00:00+00:00",
            "tripwire": {"events": [], "member_clusters": [], "all_clusters": []},
            "laminar": {"events": [], "traces": []},
        }
        base.update(overrides)
        return base

    def test_four_way_bucketing_is_mutually_exclusive(self):
        snapshot = self._snapshot(
            tripwire={
                "events": [
                    {"id": 1, "laminar_trace_id": "traceBOTH", "session_id": None, "signal_name": "Issues"},
                    {"id": 2, "laminar_trace_id": "traceTWONLY", "session_id": None, "signal_name": "Issues"},
                ],
                "member_clusters": [], "all_clusters": [],
            },
            laminar={
                "events": [
                    {"trace_id": "traceBOTH", "signal_name": "Issues", "cluster_details": []},
                    {"trace_id": "traceLMONLY", "signal_name": "Issues", "cluster_details": []},
                ],
                "traces": [
                    {"id": "traceBOTH", "session_id": None},
                    {"id": "traceLMONLY", "session_id": None},
                    {"id": "traceTWONLY", "session_id": None},
                    {"id": "traceNEITHER", "session_id": None},
                ],
            },
        )
        result = compare.compare(snapshot)
        counts = result["pairs"][0]["counts"]
        self.assertEqual(counts, {"both": 1, "laminar_only": 1, "tripwire_only": 1, "neither": 1})

        # The bug this guards against: a trace must never land in two buckets at once.
        disagreements = result["pairs"][0]["disagreements"]
        laminar_only_ids = {d["trace_id"] for d in disagreements["laminar_only"]}
        tripwire_only_ids = {d["trace_id"] for d in disagreements["tripwire_only"]}
        self.assertEqual(laminar_only_ids & tripwire_only_ids, set())
        self.assertEqual(laminar_only_ids, {"traceLMONLY"})
        self.assertEqual(tripwire_only_ids, {"traceTWONLY"})

    def test_cluster_crosstab_matches_tripwire_level_0_to_laminar_level_1(self):
        snapshot = self._snapshot(
            tripwire={
                "events": [{"id": 1, "laminar_trace_id": "traceX", "session_id": None, "signal_name": "Issues"}],
                "member_clusters": [{"event_id": 1, "cluster_id": 10}],
                "all_clusters": [{"id": 10, "name": "tw cluster", "level": 0, "parent_id": None, "signal_id": 1}],
            },
            laminar={
                "events": [{
                    "trace_id": "traceX", "signal_name": "Issues",
                    "cluster_details": [{"id": "lm1", "name": "lm cluster", "level": 1}],
                }],
                "traces": [{"id": "traceX", "session_id": None}],
            },
        )
        result = compare.compare(snapshot)
        crosstab = result["pairs"][0]["cluster_crosstab"]
        self.assertEqual(crosstab, {0: {"tw cluster": {"lm cluster": 1}}})


if __name__ == "__main__":
    unittest.main()
