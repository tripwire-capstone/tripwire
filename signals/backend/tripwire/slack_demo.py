"""Add fake rows that make the Slack notifier send an alert, for demos.

    python -m tripwire.slack_demo critical   # a finished run with one critical event
    python -m tripwire.slack_demo cluster    # a named cluster holding one event

Each command adds a made-up trace, run and event to the project's first signal, so the rows
show up in the UI and the dev view like real ones. The notifier picks them up on its next
poll (every 15 s by default) if Slack is connected (python -m tripwire.slack_setup connect).

The cluster's event gets an all-zero embedding, so the clusterer leaves it alone. The
critical event has no embedding, so the clusterer summarizes and clusters it like any other.

--project picks the project by name (default: the seeded "default" project).
Settings (env or .env): DATABASE_URL.
"""

import argparse
import os
import uuid

import psycopg
from dotenv import load_dotenv

from .placeholder import vector_text
from .slack_setup import DEFAULT_PROJECT, find_project

EMBEDDING_SIZE = 1536


def latest_version(conn, project_id: int) -> tuple[int, str]:
    """(signal_version_id, signal name) of the project's first signal."""
    row = conn.execute(
        """
        SELECT v.id, s.name
        FROM signals AS s
        JOIN signal_versions AS v ON v.signal_id = s.id
        WHERE s.project_id = %s
        ORDER BY s.id, v.version_number DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row is None:
        raise SystemExit("The project has no signals. Run the seed first: python -m tripwire.seed")
    return row


def add_event(conn, project_id: int, version_id: int, severity: str, description: str) -> tuple[int, int]:
    """Insert a fake trace, a finished run and one event. Returns (run_id, event_id)."""
    (trace_id,) = conn.execute(
        """
        INSERT INTO traces (project_id, laminar_trace_id, session_id, started_at, ended_at, status,
                            metadata)
        VALUES (%s, %s, 'slack-demo', now() - interval '1 minute', now(), 'success',
                '{"source": "slack_demo"}')
        RETURNING id
        """,
        (project_id, uuid.uuid4()),
    ).fetchone()
    (run_id,) = conn.execute(
        """
        INSERT INTO signal_runs (trace_id, signal_version_id, status, attempts, model,
                                 started_at, finished_at, analysis)
        VALUES (%s, %s, 'done', 1, 'slack-demo', now(), now(), 'Added by tripwire.slack_demo')
        RETURNING id
        """,
        (trace_id, version_id),
    ).fetchone()
    (event_id,) = conn.execute(
        """
        INSERT INTO signal_events (run_id, trace_id, signal_version_id, severity, description,
                                   judge_input)
        VALUES (%s, %s, %s, %s, %s, 'Added by tripwire.slack_demo')
        RETURNING id
        """,
        (run_id, trace_id, version_id, severity, description),
    ).fetchone()
    return run_id, event_id


def add_critical_run(conn, project_id: int) -> str:
    version_id, signal_name = latest_version(conn, project_id)
    run_id, _ = add_event(conn, project_id, version_id, "critical",
                          "Demo: the user gave up after the assistant ignored three corrections.")
    return f"Added run #{run_id} with one critical event to {signal_name!r}"


def add_cluster(conn, project_id: int) -> str:
    version_id, signal_name = latest_version(conn, project_id)
    _, event_id = add_event(conn, project_id, version_id, "warning",
                            "Demo: the user repeated the same question twice.")
    zeros = vector_text([0.0] * EMBEDDING_SIZE)
    conn.execute(
        "INSERT INTO event_embeddings (event_id, model, embedding) VALUES (%s, 'slack-demo', %s::vector)",
        (event_id, zeros),
    )
    (cluster_id,) = conn.execute(
        """
        INSERT INTO clusters (signal_id, name, description)
        SELECT signal_id, 'Demo: users repeating themselves', 'Added by tripwire.slack_demo'
        FROM signal_versions WHERE id = %s
        RETURNING id
        """,
        (version_id,),
    ).fetchone()
    conn.execute("INSERT INTO cluster_members (event_id, cluster_id) VALUES (%s, %s)",
                 (event_id, cluster_id))
    return f"Added cluster #{cluster_id} with one event to {signal_name!r}"


def main(argv=None):
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m tripwire.slack_demo",
                                     description="Add fake rows that trigger a Slack alert.")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="project name (default: %(default)s)")
    parser.add_argument("kind", choices=["critical", "cluster"],
                        help="critical: a run with a critical event; cluster: a new named cluster")
    args = parser.parse_args(argv)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        project_id = find_project(conn, args.project)
        add = add_critical_run if args.kind == "critical" else add_cluster
        message = add(conn, project_id)
    print(message)


if __name__ == "__main__":
    main()
