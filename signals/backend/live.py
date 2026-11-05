"""Read-only queries for the Live view: scored live turns and how many are still waiting."""

import os

from classifier.config import QUESTION_VERSION, SIGNAL

LIVE_RESULTS_SQL = """
SELECT t.id, t.item, t.trace_id, t.conversation_id, t.target_index, t.turn_ended_at,
       r.probability
FROM turns AS t
JOIN results AS r
    ON r.turn_id = t.id AND r.signal = %s AND r.question_version = %s
WHERE t.source = 'live'
ORDER BY r.probability DESC, t.turn_ended_at DESC
LIMIT %s
"""

PENDING_LIVE_SQL = """
SELECT count(*) AS pending
FROM turns AS t
LEFT JOIN results AS r
    ON r.turn_id = t.id AND r.signal = %s AND r.question_version = %s
WHERE t.source = 'live' AND r.turn_id IS NULL
"""


def laminar_url(trace_id):
    # e.g. http://localhost:5667/project/<project id>, from the root .env. Unset: no link.
    project_url = os.environ.get("LMNR_UI_PROJECT_URL", "").rstrip("/")
    return f"{project_url}/traces?traceId={trace_id}" if project_url else None


def load_live(connection, limit=200):
    rows = connection.execute(LIVE_RESULTS_SQL, (SIGNAL, QUESTION_VERSION, limit)).fetchall()
    pending = connection.execute(PENDING_LIVE_SQL, (SIGNAL, QUESTION_VERSION)).fetchone()["pending"]
    return {
        "signal": SIGNAL,
        "question_version": QUESTION_VERSION,
        # Live turns synced but not scored yet (classifier not running, or still catching up).
        "pending_turns": pending,
        "turns": [
            {
                "turn_id": row["id"],
                "probability": row["probability"],
                "trace_id": str(row["trace_id"]),
                "laminar_url": laminar_url(row["trace_id"]),
                "conversation_id": row["conversation_id"],
                "target_index": row["target_index"],
                "turn_ended_at": row["turn_ended_at"].isoformat(),
                "user_message": row["item"]["turns"][-1]["user"],
                "conversation": row["item"],
            }
            for row in rows
        ],
    }
