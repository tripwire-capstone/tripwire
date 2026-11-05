"""Live classification: synthetic turns and a mocked classifier; never calls OpenAI."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import openai
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from classifier import frustration, live
from eval import storage
from sync.items import build_item

ROOT = Path(__file__).resolve().parents[1]
SMOKE_ROWS = json.loads((ROOT / "sync" / "tests" / "fixtures" / "smoke_convo.json").read_text())
SMOKE_SESSION = "smoke-session"


def live_item(n):
    trace_id = str(uuid4())
    return {
        "contract": "v1",
        "conversation_id": f"live-{n}",
        "target_turn": 1,
        "history_truncated": False,
        "turns": [
            {
                "index": 1,
                "trace_id": trace_id,
                "started_at": "2026-10-26T18:00:00Z",
                "user": f"Live message {n}",
                "tools": [],
                "reply": "Reply",
            }
        ],
    }


def api_error(cls, status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.example"))
    return cls("simulated", response=response, body=None)


class LiveItemShapeTests(unittest.TestCase):
    def test_sync_item_reaches_classifier_as_history_plus_target_user_message(self):
        # The seam between the tracks: a real item built by sync goes through the
        # classifier's input-building unchanged. No API call.
        target = SMOKE_ROWS[-1]["trace_id"]
        item = build_item(SMOKE_ROWS, target, SMOKE_SESSION)
        with patch.object(frustration, "get_frustration_probability", return_value=0.5) as ask:
            frustration.classify(item)
        messages = ask.call_args.args[0]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user", "assistant", "user"])
        self.assertEqual(messages[-1]["content"], item["turns"][-1]["user"])
        # Neither the target's reply nor any tool output reaches the classifier.
        sent = json.dumps(messages)
        self.assertNotIn(item["turns"][-1]["reply"], sent)
        self.assertNotIn("park_name", sent)


class LiveClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = f"signals_tests_{uuid4().hex}"
        admin = psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True)
        cls.addClassCleanup(admin.close)
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.addClassCleanup(
            admin.execute,
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)),
        )
        cls.database_url = make_conninfo(
            os.environ["TEST_DATABASE_URL"], options=f"-c search_path={cls.schema}"
        )
        with psycopg.connect(cls.database_url, autocommit=True) as connection:
            connection.execute((ROOT / "schema.sql").read_text())

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"DATABASE_URL": self.database_url}))
        self.connection = self.enterContext(storage.connect_database())
        self.connection.execute("TRUNCATE results, turns RESTART IDENTITY")
        self.skipped = set()

    def add_live(self, n):
        return self.connection.execute(
            "INSERT INTO turns (source, item, turn_ended_at) "
            "VALUES ('live', %s, now() + %s * interval '1 second') RETURNING id",
            (Jsonb(live_item(n)), n),
        ).fetchone()["id"]

    def add_gold(self):
        item = live_item(99)
        item["turns"][0]["trace_id"] = None
        self.connection.execute(
            "INSERT INTO turns (source, item, gold_case_id) VALUES ('gold', %s, 'gold-1')",
            (Jsonb(item),),
        )

    def scores(self):
        rows = self.connection.execute(
            "SELECT turn_id, question_version, probability FROM results ORDER BY turn_id, question_version"
        ).fetchall()
        return [(r["turn_id"], r["question_version"], round(r["probability"], 2)) for r in rows]

    def run_pass(self, classify_item, **kwargs):
        return live.classify_pending(self.connection, self.skipped, classify_item=classify_item, **kwargs)

    def test_scores_unscored_live_turns_once_and_ignores_gold(self):
        a, b = self.add_live(1), self.add_live(2)
        self.add_gold()
        calls = []

        def fake(item):
            calls.append(item["conversation_id"])
            return 0.8

        first = self.run_pass(fake)
        second = self.run_pass(fake)
        self.assertEqual(calls, ["live-1", "live-2"])  # oldest first; none on the 2nd pass
        self.assertEqual((first.scored, second.found), (2, 0))
        self.assertEqual(self.scores(), [(a, "v1", 0.8), (b, "v1", 0.8)])

    def test_turn_written_between_passes_is_picked_up(self):
        self.add_live(1)
        self.run_pass(lambda item: 0.1)
        c = self.add_live(2)
        result = self.run_pass(lambda item: 0.2)
        self.assertEqual(result.scored, 1)
        self.assertIn((c, "v1", 0.2), self.scores())

    def test_new_question_version_rescores_without_touching_old_scores(self):
        a = self.add_live(1)
        self.run_pass(lambda item: 0.3)
        with patch.object(live, "QUESTION_VERSION", "v2"), patch.object(storage, "QUESTION_VERSION", "v2"):
            self.run_pass(lambda item: 0.9)
        self.assertEqual(self.scores(), [(a, "v1", 0.3), (a, "v2", 0.9)])

    def test_failing_turn_is_skipped_for_the_run_and_others_still_scored(self):
        a, b, c = self.add_live(1), self.add_live(2), self.add_live(3)
        calls = []

        def fake(item):
            calls.append(item["conversation_id"])
            if item["conversation_id"] == "live-2":
                raise RuntimeError("Expected a probability answer, got refusal")
            return 0.6

        first = self.run_pass(fake)
        second = self.run_pass(fake)
        self.assertEqual((first.scored, first.failed), (2, 1))
        self.assertEqual(second.found, 0)  # not retried in the same run
        self.assertEqual(calls, ["live-1", "live-2", "live-3"])
        self.assertEqual(self.skipped, {b})
        self.assertEqual([s[0] for s in self.scores()], [a, c])
        # A restart (empty skip set) tries it again.
        self.skipped.clear()
        self.assertEqual(self.run_pass(lambda item: 0.4).scored, 1)

    def test_skipped_turns_do_not_block_newer_turns_past_the_limit(self):
        self.add_live(1)
        self.add_live(2)
        b = self.add_live(3)

        def refuse_first_two(item):
            if item["conversation_id"] in ("live-1", "live-2"):
                raise RuntimeError("refusal")
            return 0.5

        self.run_pass(refuse_first_two, limit=2)
        result = self.run_pass(refuse_first_two, limit=2)
        self.assertEqual(result.scored, 1)
        self.assertEqual([s[0] for s in self.scores()], [b])

    def test_out_of_range_probability_is_a_turn_error(self):
        self.add_live(1)
        result = self.run_pass(lambda item: 1.7)
        self.assertEqual((result.scored, result.failed), (0, 1))
        self.assertEqual(self.scores(), [])

    def test_rate_limit_stops_the_pass_and_keeps_the_turn_for_next_pass(self):
        a = self.add_live(1)
        self.add_live(2)

        def rate_limited(item):
            raise api_error(openai.RateLimitError, 429)

        result = self.run_pass(rate_limited)
        self.assertTrue(result.stopped_early)
        self.assertEqual((result.scored, result.failed), (0, 0))
        self.assertEqual(self.skipped, set())  # temporary: not skipped
        self.assertEqual(self.run_pass(lambda item: 0.5).scored, 2)
        self.assertEqual(self.scores()[0][0], a)

    def test_setup_errors_stop_the_process(self):
        self.add_live(1)
        for error in (
            api_error(openai.AuthenticationError, 401),
            api_error(openai.NotFoundError, 404),
            openai.OpenAIError("Missing credentials"),
        ):
            with self.subTest(error=type(error).__name__):
                def broken(item, error=error):
                    raise error

                with self.assertRaises(type(error)):
                    self.run_pass(broken)
        self.assertEqual(self.scores(), [])


if __name__ == "__main__":
    unittest.main()
