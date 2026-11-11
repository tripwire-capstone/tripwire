"""Save a Laminar trace as an offline judge fixture."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from tripwire.poller.poll import parse_laminar_time

from .fetch import fetch_earlier_turns, fetch_spans

SESSION_TRACES_SQL = """
SELECT id AS trace_id, session_id,
       toString(start_time, 'UTC') AS started_utc
FROM traces
WHERE session_id IN {sessions:Array(String)}
  AND start_time <= toDateTime64({until:String}, 6, 'UTC')
  AND end_time <= toDateTime64({until:String}, 6, 'UTC')
  AND end_time >= start_time
ORDER BY end_time DESC, id DESC
LIMIT {limit:UInt32}
"""


def export_trace(
    laminar,
    *,
    trace_id: str,
    session_id: str | None,
    started_at: datetime,
    output_dir: Path,
) -> Path:
    trace_id = str(UUID(trace_id))
    spans = fetch_spans(laminar, trace_id, started_at)
    earlier_turns = fetch_earlier_turns(laminar, session_id, started_at)
    fixture = {
        "trace": {
            "trace_id": trace_id,
            "session_id": session_id,
            "started_at": started_at.astimezone(timezone.utc).isoformat(),
        },
        "spans": spans,
        "earlier_turns": earlier_turns,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{trace_id}.json"
    path.write_text(
        json.dumps(fixture, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def export_sessions(
    laminar, *, session_ids: list[str], output_dir: Path, limit: int = 50
) -> list[Path]:
    """Export the newest finished traces across the selected sessions."""
    if not session_ids or not 1 <= limit <= 2**32 - 1:
        raise ValueError("Provide session IDs and a positive UInt32 limit")

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=15)
    rows = laminar.query(
        SESSION_TRACES_SQL,
        {
            "sessions": session_ids,
            "until": cutoff.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "limit": limit,
        },
    )
    return [
        export_trace(
            laminar,
            trace_id=row["trace_id"],
            session_id=row["session_id"],
            started_at=parse_laminar_time(row["started_utc"]),
            output_dir=output_dir,
        )
        for row in rows
    ]


def main() -> None:
    import argparse
    import os

    from dotenv import load_dotenv
    from tripwire.poller.laminar import LaminarSQL

    parser = argparse.ArgumentParser(description="Export Laminar judge fixtures")
    parser.add_argument("--session", action="append", required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    if not 1 <= args.limit <= 2**32 - 1:
        parser.error("--limit must be between 1 and 4294967295")

    backend_dir = Path(__file__).resolve().parents[2]
    load_dotenv(backend_dir / ".env")
    key = os.getenv("LMNR_PROJECT_API_KEY")
    if not key:
        parser.error("LMNR_PROJECT_API_KEY is required")

    laminar = LaminarSQL(key, os.getenv("LMNR_API_URL") or "https://api.lmnr.ai")
    try:
        paths = export_sessions(
            laminar,
            session_ids=args.session,
            output_dir=backend_dir / "tests" / "fixtures" / "traces",
            limit=args.limit,
        )
    finally:
        laminar.close()

    print(f"Exported {len(paths)} fixtures")


if __name__ == "__main__":
    main()
