"""Shared database access for the evaluator and the read-only dashboard API."""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from classifier.config import QUESTION_VERSION, SIGNAL

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "eval" / "gold" / "frustration-v2.jsonl"


def load_gold(split):
    if split not in ("dev", "test"):
        raise ValueError("split must be dev or test")
    cases = []
    with GOLD.open() as source:
        for line in source:
            if not line.strip():
                continue
            case = json.loads(line)
            if case["split"] == split:
                cases.append(case)
    return cases


def connect_database(*, read_only=False):
    load_dotenv(ROOT / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("Set DATABASE_URL in the project's root .env file.")
    connection = psycopg.connect(
        database_url,
        autocommit=not read_only,
        row_factory=dict_row,
        connect_timeout=5,
    )
    if read_only:
        connection.read_only = True
    return connection


def seed_gold(connection, cases):
    # Seed only the requested split. The labels and source metadata stay out of item.
    with connection.transaction():
        for case in cases:
            connection.execute(
                """
                INSERT INTO turns (source, item, gold_case_id)
                VALUES ('gold', %s, %s)
                ON CONFLICT (gold_case_id) DO NOTHING
                """,
                (Jsonb(case["conversation"]), case["case_id"]),
            )


def load_rows(connection, cases):
    rows = connection.execute(
        """
        SELECT t.id, t.gold_case_id, t.item, t.trace_id, r.probability
        FROM turns AS t
        LEFT JOIN results AS r
            ON r.turn_id = t.id AND r.signal = %s AND r.question_version = %s
        WHERE t.source = 'gold' AND t.gold_case_id = ANY(%s)
        """,
        (SIGNAL, QUESTION_VERSION, [case["case_id"] for case in cases]),
    ).fetchall()
    by_case_id = {row["gold_case_id"]: row for row in rows}
    ordered_rows = []
    for case in cases:
        row = by_case_id.get(case["case_id"])
        if row is not None:
            if row["item"] != case["conversation"]:
                raise ValueError(
                    f"Stored turn {case['case_id']} differs from the gold file. "
                    "Reload that gold turn before reusing its score."
                )
            ordered_rows.append(row)
    return ordered_rows


def save_probability(connection, turn_id, probability):
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("Classifier probability must be a finite number between 0 and 1.")
    # Each successful score is committed immediately, so a failed run can resume.
    connection.execute(
        """
        INSERT INTO results (turn_id, signal, question_version, probability)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (turn_id, signal, question_version) DO NOTHING
        """,
        (turn_id, SIGNAL, QUESTION_VERSION, probability),
    )


def load_report(connection, cases, split, threshold=0.5):
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    labels = {case["case_id"]: case["expected"] for case in cases}
    evaluated = []
    counts = {"hit": 0, "miss": 0, "false_alarm": 0, "correct_no": 0}
    for row in load_rows(connection, cases):
        if row["probability"] is None:
            continue
        expected = labels[row["gold_case_id"]]
        predicted = row["probability"] >= threshold
        if expected == "yes":
            outcome = "hit" if predicted else "miss"
        else:
            outcome = "false_alarm" if predicted else "correct_no"
        counts[outcome] += 1
        evaluated.append(
            {
                "case_id": row["gold_case_id"],
                "expected": expected,
                "probability": row["probability"],
                "predicted": "yes" if predicted else "no",
                "outcome": outcome,
                "user_message": row["item"]["turns"][-1]["user"],
                "conversation": row["item"],
                "trace_id": str(row["trace_id"]) if row["trace_id"] else None,
            }
        )
    flagged = counts["hit"] + counts["false_alarm"]
    actual_yes = counts["hit"] + counts["miss"]
    return {
        "signal": SIGNAL,
        "question_version": QUESTION_VERSION,
        "split": split,
        "threshold": threshold,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(cases),
        "pending_cases": len(cases) - len(evaluated),
        "metrics": {
            "total_cases": len(evaluated),
            "hits": counts["hit"],
            "misses": counts["miss"],
            "false_alarms": counts["false_alarm"],
            "correct_negatives": counts["correct_no"],
            "precision": counts["hit"] / flagged if flagged else 0,
            "recall": counts["hit"] / actual_yes if actual_yes else 0,
        },
        "cases": evaluated,
    }
