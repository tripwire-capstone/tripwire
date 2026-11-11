"""Test data for the clusterer tests: signals, events with embeddings, clusters.

Kept in one place so a schema change on the Signals pair's tables (e.g. several events per run,
or `description` becoming optional) means fixing one function, not every test file.
"""

import uuid

from tripwire.placeholder import vector_text


def project_id(conn):
    return conn.execute("SELECT id FROM projects WHERE name = 'test'").fetchone()[0]


def add_signal(conn, name="Signal A"):
    """A signal with one version. Returns (signal_id, version_id)."""
    signal_id = conn.execute(
        "INSERT INTO signals (project_id, name) VALUES (%s, %s) RETURNING id",
        (project_id(conn), name),
    ).fetchone()[0]
    return signal_id, add_version(conn, signal_id, 1)


def add_version(conn, signal_id, version_number):
    return conn.execute(
        """INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, model)
           VALUES (%s, %s, 'test instruction', '{}', 'test-model') RETURNING id""",
        (signal_id, version_number),
    ).fetchone()[0]


def add_event(conn, version_id, embedding, session_id=None, created_days_ago=0):
    """A trace (in `session_id`, or none), a done run, an event and its embedding.

    `created_days_ago` backdates the event's created_at, for pool window tests.
    Returns the event ID.
    """
    trace_id = conn.execute(
        """INSERT INTO traces (project_id, laminar_trace_id, session_id, started_at, ended_at, status)
           VALUES (%s, %s, %s, now() - interval '1 minute', now(), 'success') RETURNING id""",
        (project_id(conn), str(uuid.uuid4()), session_id),
    ).fetchone()[0]
    run_id = conn.execute(
        """INSERT INTO signal_runs (trace_id, signal_version_id, status, finished_at)
           VALUES (%s, %s, 'done', now()) RETURNING id""",
        (trace_id, version_id),
    ).fetchone()[0]
    event_id = conn.execute(
        """INSERT INTO signal_events (run_id, trace_id, signal_version_id, severity, description,
                                      judge_input, created_at)
           VALUES (%s, %s, %s, 'warning', 'test event', 'test input',
                   now() - make_interval(days => %s))
           RETURNING id""",
        (run_id, trace_id, version_id, created_days_ago),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO event_embeddings (event_id, model, embedding) VALUES (%s, 'test', %s)",
        (event_id, vector_text(embedding)),
    )
    return event_id


def add_cluster(conn, signal_id, centroid, level=0):
    return conn.execute(
        "INSERT INTO clusters (signal_id, level, centroid) VALUES (%s, %s, %s) RETURNING id",
        (signal_id, level, None if centroid is None else vector_text(centroid)),
    ).fetchone()[0]


def cluster_of(conn, event_id):
    """The event's cluster ID, or None if it's in the pool."""
    row = conn.execute(
        "SELECT cluster_id FROM cluster_members WHERE event_id = %s", (event_id,)
    ).fetchone()
    return None if row is None else row[0]


def clusters_of_signal(conn, signal_id):
    """{cluster_id: sorted member event IDs} for one Signal."""
    rows = conn.execute(
        """SELECT cluster.id, member.event_id
           FROM clusters cluster JOIN cluster_members member ON member.cluster_id = cluster.id
           WHERE cluster.signal_id = %s ORDER BY cluster.id, member.event_id""",
        (signal_id,),
    ).fetchall()
    clusters = {}
    for cluster_id, event_id in rows:
        clusters.setdefault(cluster_id, []).append(event_id)
    return clusters


def cosine_distance(conn, vector_a, vector_b):
    return conn.execute(
        "SELECT %s::vector <=> %s::vector", (vector_text(vector_a), vector_text(vector_b))
    ).fetchone()[0]
