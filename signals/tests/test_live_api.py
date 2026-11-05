"""GET /api/live: synthetic turns and scores; read-only, never classifies."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from backend.app import app
from eval import storage
from tests.test_live import ROOT, live_item


class LiveApiTests(unittest.TestCase):
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
        self.client = TestClient(app)

    def add_live(self, n, probability=None, version="v1"):
        turn_id = self.connection.execute(
            "INSERT INTO turns (source, item, turn_ended_at) VALUES ('live', %s, now()) RETURNING id",
            (Jsonb(live_item(n)),),
        ).fetchone()["id"]
        if probability is not None:
            self.connection.execute(
                "INSERT INTO results VALUES (%s, 'user_frustration', %s, %s)",
                (turn_id, version, probability),
            )
        return turn_id

    def get(self, **params):
        response = self.client.get("/api/live", params=params)
        return response.status_code, response.json()

    def test_scored_live_turns_highest_probability_first_with_pending_count(self):
        low = self.add_live(1, 0.2)
        high = self.add_live(2, 0.9)
        self.add_live(3)                   # synced, not scored yet
        self.add_live(4, 0.95, "v0")       # scored only under an old version: pending too
        gold = live_item(9)
        gold["turns"][0]["trace_id"] = None
        self.connection.execute(
            "INSERT INTO turns (source, item, gold_case_id) VALUES ('gold', %s, 'g1')", (Jsonb(gold),)
        )

        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual([t["turn_id"] for t in body["turns"]], [high, low])
        self.assertEqual(body["pending_turns"], 2)
        self.assertEqual(body["question_version"], "v1")
        first = body["turns"][0]
        self.assertEqual(first["user_message"], "Live message 2")
        self.assertEqual(first["conversation"], self.connection.execute(
            "SELECT item FROM turns WHERE id = %s", (high,)).fetchone()["item"])
        self.assertEqual(first["trace_id"], first["conversation"]["turns"][-1]["trace_id"])
        self.assertEqual((first["conversation_id"], first["target_index"]), ("live-2", 1))

    def test_empty_database_returns_no_turns(self):
        status, body = self.get()
        self.assertEqual((status, body["turns"], body["pending_turns"]), (200, [], 0))

    def test_reading_never_classifies_or_writes(self):
        self.add_live(1)
        with patch("classifier.frustration.get_frustration_probability",
                   side_effect=AssertionError("API must not classify")):
            status, body = self.get()
        self.assertEqual((status, body["pending_turns"]), (200, 1))
        count = self.connection.execute("SELECT count(*) AS n FROM results").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_laminar_link_comes_from_env_and_is_omitted_when_unset(self):
        self.add_live(1, 0.5)
        project = "http://localhost:5667/project/abc/"
        with patch.dict(os.environ, {"LMNR_UI_PROJECT_URL": project}):
            turn = self.get()[1]["turns"][0]
        self.assertEqual(
            turn["laminar_url"],
            f"http://localhost:5667/project/abc/traces?traceId={turn['trace_id']}",
        )
        with patch.dict(os.environ, {"LMNR_UI_PROJECT_URL": ""}):
            self.assertIsNone(self.get()[1]["turns"][0]["laminar_url"])

    def test_limit_caps_rows_and_is_validated(self):
        for n in range(3):
            self.add_live(n, 0.5)
        self.assertEqual(len(self.get(limit=2)[1]["turns"]), 2)
        self.assertEqual(self.get(limit=0)[0], 422)


if __name__ == "__main__":
    unittest.main()
