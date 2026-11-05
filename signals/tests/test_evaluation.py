"""Checks use synthetic cases and never call the real classifier service."""

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.app import app
from classifier.frustration import classify
from eval import evaluate_frustration, storage


def gold_case(case_id, split, expected):
    return {
        "case_id": case_id,
        "split": split,
        "expected": expected,
        "label_note": "Must never reach the classifier",
        "source": {"scenario_id": "answer-key-only"},
        "conversation": {
            "contract": "v1",
            "conversation_id": f"synthetic-{case_id}",
            "target_turn": 2,
            "history_truncated": False,
            "turns": [
                {
                    "index": 1,
                    "trace_id": None,
                    "started_at": None,
                    "user": "Earlier question",
                    "reply": "Earlier reply",
                    "tools": [],
                },
                {
                    "index": 2,
                    "trace_id": None,
                    "started_at": None,
                    "user": f"Current message {case_id}",
                    "reply": "Target reply must not influence frustration",
                    "tools": [{"name": "target-tool", "output": "hidden"}],
                },
            ],
        },
    }


class EvaluationSafetyTests(unittest.TestCase):
    def test_classifier_uses_history_and_current_user_without_target_response(self):
        item = gold_case("sample", "dev", "yes")["conversation"]
        original = copy.deepcopy(item)
        with patch(
            "classifier.frustration.get_frustration_probability", return_value=0.7
        ) as score:
            self.assertEqual(classify(item), 0.7)
        score.assert_called_once_with(
            [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier reply"},
                {"role": "user", "content": "Current message sample"},
            ]
        )
        self.assertEqual(item, original)

    def test_invalid_scores_are_rejected_before_writing(self):
        connection = Mock()
        for value in (-0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.save_probability(connection, 1, value)
        connection.execute.assert_not_called()

    def test_missing_database_configuration_has_actionable_message(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}), patch(
            "eval.storage.load_dotenv"
        ):
            response = TestClient(app).get("/api/results")
        self.assertEqual(response.status_code, 503)
        self.assertIn("DATABASE_URL", response.json()["detail"])

    def test_database_connection_error_does_not_expose_credentials(self):
        with patch(
            "backend.app.connect_database",
            side_effect=psycopg.OperationalError("secret-password"),
        ):
            response = TestClient(app).get("/api/results")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-password", response.text)

    def test_missing_schema_has_actionable_message(self):
        with patch(
            "backend.app.connect_database", side_effect=psycopg.errors.UndefinedTable()
        ):
            response = TestClient(app).get("/api/results")
        self.assertEqual(response.status_code, 503)
        self.assertIn("schema.sql", response.json()["detail"])

    def test_unknown_split_is_rejected(self):
        response = TestClient(app).get("/api/results?split=invalid")
        self.assertEqual(response.status_code, 422)


@unittest.skipUnless(
    os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for Postgres checks"
)
class DatabaseEvaluationTests(unittest.TestCase):
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
            connection.execute((storage.ROOT / "schema.sql").read_text())

    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"DATABASE_URL": self.database_url}))
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.cases = [
            gold_case("dev-a", "dev", "yes"),
            gold_case("dev-b", "dev", "no"),
            gold_case("test-a", "test", "no"),
            gold_case("test-b", "test", "yes"),
        ]
        gold = Path(self.temp) / "synthetic.jsonl"
        gold.write_text("\n".join(json.dumps(case) for case in self.cases) + "\n\n")
        self.enterContext(patch("eval.storage.GOLD", gold))
        self.connection = self.enterContext(storage.connect_database())
        self.connection.execute("TRUNCATE results, turns RESTART IDENTITY")
        self.client = TestClient(app)

    def evaluate(self, split="dev", threshold=0.5):
        with redirect_stdout(io.StringIO()):
            return evaluate_frustration.evaluate(
                split=split, threshold=threshold, output=Path(self.temp) / "report.json"
            )

    def count(self, table):
        return self.connection.execute(
            sql.SQL("SELECT count(*) AS count FROM {}").format(sql.Identifier(table))
        ).fetchone()["count"]

    def test_evaluator_seeds_selected_split_and_reuses_scores(self):
        with patch(
            "eval.evaluate_frustration.classify", side_effect=[0.75, 0.25]
        ) as classifier:
            report = self.evaluate()
            self.evaluate()
        self.assertEqual(classifier.call_count, 2)
        self.assertEqual(
            classifier.call_args_list[0].args[0], self.cases[0]["conversation"]
        )
        self.assertEqual(
            classifier.call_args_list[1].args[0], self.cases[1]["conversation"]
        )
        self.assertEqual(self.count("turns"), 2)
        self.assertEqual(self.count("results"), 2)
        self.assertEqual(report["pending_cases"], 0)
        self.assertEqual(report["metrics"]["precision"], 1)
        self.assertEqual(report["metrics"]["recall"], 1)

    def test_failed_run_preserves_scores_and_resumes_only_missing_turns(self):
        with patch(
            "eval.evaluate_frustration.classify",
            side_effect=[0.75, RuntimeError("interrupted")],
        ):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.evaluate()
        self.assertEqual(self.count("results"), 1)
        partial = self.client.get("/api/results").json()
        self.assertEqual(partial["pending_cases"], 1)
        with patch(
            "eval.evaluate_frustration.classify", return_value=0.25
        ) as classifier:
            self.evaluate()
        classifier.assert_called_once_with(self.cases[1]["conversation"])
        self.assertEqual(self.count("results"), 2)

    def test_new_question_version_gets_new_scores_without_overwriting_old_ones(self):
        with patch("eval.evaluate_frustration.classify", return_value=0.75):
            self.evaluate()
        with patch("eval.storage.QUESTION_VERSION", "v2"):
            self.assertEqual(self.client.get("/api/results").json()["pending_cases"], 2)
            with patch(
                "eval.evaluate_frustration.classify", return_value=0.25
            ) as classifier:
                report = self.evaluate()
            self.assertEqual(classifier.call_count, 2)
            self.assertEqual(report["question_version"], "v2")
            self.assertTrue(
                all(case["probability"] == 0.25 for case in report["cases"])
            )
        self.assertEqual(self.count("results"), 4)

    def test_api_is_read_only_and_test_split_requires_explicit_request(self):
        with patch("eval.evaluate_frustration.classify", return_value=0.75):
            self.evaluate()

        real_load_report = storage.load_report

        def check_read_only(connection, *args):
            mode = connection.execute("SHOW transaction_read_only").fetchone()
            self.assertEqual(mode["transaction_read_only"], "on")
            return real_load_report(connection, *args)

        with patch("backend.app.load_report", side_effect=check_read_only):
            with patch("eval.evaluate_frustration.classify") as classifier:
                response = self.client.get("/api/results")
                self.client.get("/api/results")
                test = self.client.get("/api/results?split=test").json()
            classifier.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        dev = response.json()
        self.assertEqual([case["case_id"] for case in dev["cases"]], ["dev-a", "dev-b"])
        self.assertEqual([case["expected"] for case in dev["cases"]], ["yes", "no"])
        self.assertEqual(test["cases"], [])
        self.assertEqual(test["pending_cases"], 2)
        self.assertEqual(self.count("turns"), 2)
        self.assertEqual(self.count("results"), 2)

        with patch("eval.evaluate_frustration.classify", return_value=0.25):
            self.evaluate(split="test")
        test = self.client.get("/api/results?split=test").json()
        self.assertEqual(
            [case["case_id"] for case in test["cases"]], ["test-a", "test-b"]
        )
        self.assertEqual([case["expected"] for case in test["cases"]], ["no", "yes"])

    def test_empty_report_does_not_insert_rows(self):
        report = self.client.get("/api/results").json()
        self.assertEqual(report["cases"], [])
        self.assertEqual(report["total_cases"], 2)
        self.assertEqual(report["pending_cases"], 2)
        self.assertEqual(self.count("turns"), 0)

    def test_threshold_changes_metrics_without_classifying_again(self):
        with patch(
            "eval.evaluate_frustration.classify", return_value=0.75
        ) as classifier:
            first = self.evaluate(threshold=0.5)
            second = self.evaluate(threshold=0.8)
        self.assertEqual(classifier.call_count, 2)
        self.assertEqual(first["metrics"]["hits"], 1)
        self.assertEqual(first["metrics"]["false_alarms"], 1)
        self.assertEqual(first["metrics"]["precision"], 0.5)
        self.assertEqual(second["metrics"]["misses"], 1)
        self.assertEqual(second["metrics"]["correct_negatives"], 1)

    def test_changed_gold_item_is_not_silently_matched_to_old_score(self):
        storage.seed_gold(self.connection, self.cases[:2])
        changed = copy.deepcopy(self.cases[:2])
        changed[0]["conversation"]["turns"][-1]["user"] = "Changed input"
        with self.assertRaisesRegex(ValueError, "differs from the gold file"):
            storage.load_rows(self.connection, changed)


if __name__ == "__main__":
    unittest.main()
