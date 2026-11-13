"""Tripwire's HTTP API: signals (create, read, edit, on/off), their events and runs, and test-on-trace.

Run: uvicorn tripwire.api.app:app --host 0.0.0.0 --port 8080
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from ..clusterer.sql import leaf_cluster
from ..judge.config import JudgeModelConfigError, configured_judge_model
from ..judge.judge import judge
from ..judge.types import JudgeError, SignalVersion, TraceRow
from ..judge.laminar import JudgeLaminarSQL
from .db import get_conn
from .validate import normalize_fields, problems

log = logging.getLogger("api")
app = FastAPI(title="Tripwire")


# ------------------------------------------------------------------ request and response shapes

class SignalEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    instruction: str
    output_schema: list
    filters: list = []
    critical_guidance: str | None = None
    model: str | None = None


class SignalCreate(SignalEdit):
    template: str | None = None


class ActiveChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool


class SignalOut(BaseModel):
    """Exactly what the API returns for a signal: nothing else from the database goes out."""
    id: int
    name: str
    active: bool
    template: str | None
    created_at: datetime
    version_id: int
    version_number: int
    instruction: str
    output_schema: list
    filters: list
    critical_guidance: str | None
    model: str
    version_created_at: datetime


# ------------------------------------------------------------------ SQL

CURRENT_SQL = """
SELECT s.id, s.name, s.active, s.template, s.created_at,
       v.id AS version_id, v.version_number, v.instruction, v.output_schema, v.filters,
       v.critical_guidance, v.model, v.created_at AS version_created_at
FROM signals s
JOIN LATERAL (SELECT * FROM signal_versions WHERE signal_id = s.id
              ORDER BY version_number DESC LIMIT 1) v ON true
WHERE s.id = %s
"""

INSERT_VERSION_SQL = """
INSERT INTO signal_versions (signal_id, version_number, instruction, output_schema, filters,
                             critical_guidance, model)
VALUES (%(signal_id)s, %(number)s, %(instruction)s, %(output_schema)s, %(filters)s,
        %(critical_guidance)s, %(model)s)
"""


# ------------------------------------------------------------------ helpers

def check(body):
    found = problems(body.name, body.instruction, body.output_schema, body.filters,
                     body.critical_guidance)
    if found:
        raise HTTPException(422, found)


UNUSABLE_MODELS = {"", "stub", "placeholder"}


def definition(body, current=None):
    """The parts of a signal that make up a version, in their stored form.

    On an edit (`current` = the signal's current version), a field the request leaves out keeps
    its current value: `model` and `critical_guidance` aren't on the form, so an edit that omits
    them must not reset the model to the default or drop the guidance (Laminar's update works the
    same way). Sending `critical_guidance: null` clears it.
    """
    if current is not None:
        given = body.model_fields_set
        # A stored model that was never usable (the old seed's 'placeholder') isn't kept: the edit
        # moves the signal to the configured default instead.
        if "model" not in given and current["model"] not in UNUSABLE_MODELS:
            body = body.model_copy(update={"model": current["model"]})
        if "critical_guidance" not in given:
            body = body.model_copy(update={"critical_guidance": current["critical_guidance"]})
    model = (body.model or "").strip()
    if not model:
        try:
            model = configured_judge_model()
        except JudgeModelConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"instruction": body.instruction.strip(),
            "output_schema": normalize_fields(body.output_schema),
            "filters": body.filters,
            "critical_guidance": body.critical_guidance.strip() if body.critical_guidance else None,
            "model": model}


def stored(params):
    return {**params, "output_schema": Jsonb(params["output_schema"]), "filters": Jsonb(params["filters"])}


def the_project(conn):
    rows = conn.execute("SELECT id FROM projects ORDER BY id LIMIT 2").fetchall()
    if not rows:
        raise HTTPException(503, "no Laminar project yet: run the seed first")
    if len(rows) > 1:
        log.error("more than one row in projects; the API supports one project")
        raise HTTPException(500, "server misconfigured")
    return rows[0][0]


def current_row(conn, signal_id):
    row = conn.cursor(row_factory=dict_row).execute(CURRENT_SQL, (signal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "signal not found")
    return row


def current(conn, signal_id):
    return SignalOut(**current_row(conn, signal_id))


def duplicate_name(name):
    return HTTPException(409, f"a signal named '{name}' already exists")


# ------------------------------------------------------------------ routes

@app.get("/api/health")
def health(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"ok": True}


@app.post("/api/signals", status_code=201, response_model=SignalOut)
def create_signal(body: SignalCreate, conn=Depends(get_conn)):
    check(body)
    project_id = the_project(conn)
    name = body.name.strip()
    new = definition(body)
    try:
        with conn.transaction():
            signal_id = conn.execute(
                "INSERT INTO signals (project_id, name, template) VALUES (%s, %s, %s) RETURNING id",
                (project_id, name, body.template)).fetchone()[0]
            conn.execute(INSERT_VERSION_SQL, stored({"signal_id": signal_id, "number": 1, **new}))
    except psycopg.errors.UniqueViolation:
        raise duplicate_name(name)
    return current(conn, signal_id)


@app.get("/api/signals/{signal_id}", response_model=SignalOut)
def get_signal(signal_id: int, conn=Depends(get_conn)):
    return current(conn, signal_id)


@app.put("/api/signals/{signal_id}", response_model=SignalOut)
def edit_signal(signal_id: int, body: SignalEdit, conn=Depends(get_conn)):
    check(body)
    name = body.name.strip()
    try:
        with conn.transaction():
            # Lock the signal so two edits at once take turns instead of both making "version N+1".
            if conn.execute("SELECT 1 FROM signals WHERE id = %s FOR UPDATE", (signal_id,)).fetchone() is None:
                raise HTTPException(404, "signal not found")
            # A replacement definition can upgrade a legacy schema to the current field list.
            now = current_row(conn, signal_id)
            new = definition(body, current=now)
            if name != now["name"]:
                conn.execute("UPDATE signals SET name = %s WHERE id = %s", (name, signal_id))
            old = {key: now[key] for key in new}
            if new != old:
                conn.execute(INSERT_VERSION_SQL, stored({"signal_id": signal_id,
                                                         "number": now["version_number"] + 1, **new}))
    except psycopg.errors.UniqueViolation:
        raise duplicate_name(name)
    return current(conn, signal_id)


@app.patch("/api/signals/{signal_id}", response_model=SignalOut)
def set_active(signal_id: int, body: ActiveChange, conn=Depends(get_conn)):
    if conn.execute("UPDATE signals SET active = %s WHERE id = %s",
                    (body.active, signal_id)).rowcount == 0:
        raise HTTPException(404, "signal not found")
    return current(conn, signal_id)

# ------------------------------------------------------------------ reading: lists, events, runs

DEFAULT_RANGE = timedelta(days=7)
MAX_LIMIT = 500


class SignalSummary(BaseModel):
    id: int
    name: str
    active: bool
    template: str | None
    created_at: datetime
    version_number: int
    runs: int
    events: int
    problem_events: int          # warning or critical
    last_event_at: datetime | None


class EventOut(BaseModel):
    id: int
    run_id: int
    finding_index: int
    severity: str
    payload: dict
    evidence_labels: list[str]
    span_ids: list[str]
    version_number: int
    source: str
    laminar_trace_id: str
    session_id: str | None
    trace_ended_at: datetime
    cluster_id: int | None
    created_at: datetime
    laminar_url: str | None


class RunOut(BaseModel):
    id: int
    status: str
    source: str
    attempts: int
    events: int
    model: str | None
    cost_usd: float | None
    last_error: str | None
    version_number: int
    laminar_trace_id: str
    trace_ended_at: datetime
    created_at: datetime
    finished_at: datetime | None
    laminar_url: str | None


# Every count and list below is by when the turn happened (trace end), so backfilled runs land on
# their trace's day. "Problem" = warning or critical. Totals and charts are always counted in SQL:
# a page never counts rows it loaded, so a busy time range can't make a number silently low.

class SignalListItem(SignalSummary):
    clusters: int                 # leaf clusters with at least one event in the range
    activity: list[int]           # events per slice of the range, ACTIVITY_SLICES slices, oldest first


class EventPage(BaseModel):
    total: int                    # events matching the filters, before paging
    events: list[EventOut]


class RunPage(BaseModel):
    total: int
    runs: list[RunOut]


class EventCount(BaseModel):
    bucket: datetime              # start of the bucket, aligned to the clock like the UI's makeBuckets
    cluster_id: int | None        # None = not in a cluster
    events: int


class RunCount(BaseModel):
    bucket: datetime
    event: int                    # finished runs that found at least one event
    nothing: int                  # finished runs that found nothing
    failed: int


class ClusterOut(BaseModel):
    id: int
    name: str | None              # None until the clusterer names it (moments after it forms)
    events: int                   # in the range
    problem_events: int
    first_event_at: datetime
    last_event_at: datetime


class ClusterList(BaseModel):
    events: int                   # all the signal's events in the range
    clustered_events: int         # of those, how many are in a cluster
    clusters: list[ClusterOut]    # most events first


ACTIVITY_SLICES = 24

SIGNALS_SQL = """
SELECT s.id, s.name, s.active, s.template, s.created_at, v.version_number,
       coalesce(ru.runs, 0) AS runs, coalesce(ev.events, 0) AS events,
       coalesce(ev.problem_events, 0) AS problem_events, ev.last_event_at,
       coalesce(ev.clusters, 0) AS clusters
FROM signals s
JOIN LATERAL (SELECT version_number FROM signal_versions WHERE signal_id = s.id
              ORDER BY version_number DESC LIMIT 1) v ON true
LEFT JOIN LATERAL (
    SELECT count(*) AS runs
    FROM signal_runs r
    JOIN signal_versions sv ON sv.id = r.signal_version_id
    JOIN traces t ON t.id = r.trace_id
    WHERE sv.signal_id = s.id AND t.ended_at >= %(since)s
      AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
) ru ON true
LEFT JOIN LATERAL (
    SELECT count(*) AS events,
           count(*) FILTER (WHERE e.severity IN ('warning', 'critical')) AS problem_events,
           max(e.created_at) AS last_event_at,      -- when the judge last flagged something
           count(DISTINCT cm.cluster_id) AS clusters
    FROM signal_events e
    JOIN signal_versions sv ON sv.id = e.signal_version_id
    JOIN traces t ON t.id = e.trace_id
    LEFT JOIN cluster_members cm ON cm.event_id = e.id
    WHERE sv.signal_id = s.id AND t.ended_at >= %(since)s
      AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
) ev ON true
WHERE s.project_id = %(project)s
ORDER BY s.created_at, s.id
"""

# Events per signal per slice of the range (1..ACTIVITY_SLICES), for the list's Activity sparkline.
ACTIVITY_SQL = """
SELECT v.signal_id,
       least(width_bucket(extract(epoch FROM t.ended_at), extract(epoch FROM %(since)s::timestamptz),
                          extract(epoch FROM %(end)s::timestamptz), %(slices)s), %(slices)s) AS slice,
       count(*) AS events
FROM signal_events e
JOIN signal_versions v ON v.id = e.signal_version_id
JOIN traces t ON t.id = e.trace_id
WHERE t.ended_at >= %(since)s AND t.ended_at < %(end)s
GROUP BY 1, 2
"""

EVENT_COLUMNS = """
SELECT e.id, e.run_id, e.finding_index, e.severity, e.payload, e.evidence_labels, e.span_ids,
       v.version_number, r.source, t.laminar_trace_id::text AS laminar_trace_id, t.session_id,
       t.ended_at AS trace_ended_at, cm.cluster_id, e.created_at
"""

# FROM + WHERE shared by the events list, its total and its chart, so all three always agree.
EVENTS_FROM = """
FROM signal_events e
JOIN signal_versions v ON v.id = e.signal_version_id
JOIN signal_runs r ON r.id = e.run_id
JOIN traces t ON t.id = e.trace_id
LEFT JOIN cluster_members cm ON cm.event_id = e.id
WHERE v.signal_id = %(signal)s AND t.ended_at >= %(since)s
  AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
  AND (%(severity)s::text IS NULL OR e.severity = %(severity)s)
  AND (%(cluster)s::bigint IS NULL OR cm.cluster_id = %(cluster)s)
  AND (%(pattern)s::text IS NULL
       OR e.id::text = %(q)s
       OR t.laminar_trace_id::text ILIKE %(pattern)s
       OR e.severity ILIKE %(pattern)s
       OR EXISTS (SELECT 1 FROM unnest(e.evidence_labels) label WHERE label ILIKE %(pattern)s)
       -- field values only: searching "reason" mustn't match every event with a field named reason
       OR EXISTS (SELECT 1 FROM jsonb_each_text(e.payload) field WHERE field.value ILIKE %(pattern)s))
"""

RUN_COLUMNS = """
SELECT r.id, r.status, r.source, r.attempts,
       (SELECT count(*) FROM signal_events e WHERE e.run_id = r.id) AS events,
       r.model, r.cost_usd, r.last_error, v.version_number,
       t.laminar_trace_id::text AS laminar_trace_id, t.ended_at AS trace_ended_at,
       r.created_at, r.finished_at
"""

RUNS_FROM = """
FROM signal_runs r
JOIN signal_versions v ON v.id = r.signal_version_id
JOIN traces t ON t.id = r.trace_id
WHERE v.signal_id = %(signal)s AND t.ended_at >= %(since)s
  AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
  AND (%(status)s::text IS NULL OR r.status = %(status)s)
  AND (%(source)s::text IS NULL OR r.source = %(source)s)
  AND (%(outcome)s::text IS NULL
       OR (%(outcome)s = 'event') = EXISTS (SELECT 1 FROM signal_events e WHERE e.run_id = r.id))
  AND (%(pattern)s::text IS NULL
       OR r.id::text = %(q)s
       OR t.laminar_trace_id::text ILIKE %(pattern)s
       OR r.status ILIKE %(pattern)s
       OR r.source ILIKE %(pattern)s
       OR r.last_error ILIKE %(pattern)s)
"""

# Start of the bucket a time falls in, on the caller's LOCAL clock (`tz`, an IANA name): the time is
# turned into local wall-clock time, binned there, and turned back. So hour buckets start on local hours
# and day buckets at local midnight (a daylight-saving day is one 23- or 25-hour bucket). Sub-day sizes
# divide the day from midnight; multi-day sizes count days from 2000-01-01. Same rule as the UI's
# makeBuckets(), which the UI relies on to match buckets by time.
BUCKET = ("(date_bin(make_interval(secs => %(bucket_seconds)s), t.ended_at AT TIME ZONE %(tz)s,"
          " TIMESTAMP '2000-01-01') AT TIME ZONE %(tz)s)")

CLUSTERS_SQL = f"""
SELECT c.id, c.name, count(*) AS events,
       count(*) FILTER (WHERE e.severity IN ('warning', 'critical')) AS problem_events,
       min(t.ended_at) AS first_event_at, max(t.ended_at) AS last_event_at
FROM signal_events e
JOIN signal_versions v ON v.id = e.signal_version_id
JOIN traces t ON t.id = e.trace_id
JOIN cluster_members cm ON cm.event_id = e.id
JOIN clusters c ON c.id = cm.cluster_id
WHERE v.signal_id = %(signal)s AND {leaf_cluster("c")} AND t.ended_at >= %(since)s
  AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
GROUP BY c.id, c.name
ORDER BY events DESC, c.id
"""

CLUSTER_TOTALS_SQL = """
SELECT count(*) AS events, count(cm.event_id) AS clustered_events
FROM signal_events e
JOIN signal_versions v ON v.id = e.signal_version_id
JOIN traces t ON t.id = e.trace_id
LEFT JOIN cluster_members cm ON cm.event_id = e.id
WHERE v.signal_id = %(signal)s AND t.ended_at >= %(since)s
  AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
"""


def time_range(since, until):
    return {"since": since or datetime.now(timezone.utc) - DEFAULT_RANGE, "until": until}


def search(q):
    """The search box's text as query parameters: `q` as typed (exact ID match) and `pattern`, a
    case-insensitive "contains" pattern with LIKE's wildcards escaped (so "50%" means 50%)."""
    q = (q or "").strip()
    if not q:
        return {"q": None, "pattern": None}
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return {"q": q, "pattern": f"%{escaped}%"}


def known_zone(conn, tz):
    """`tz` if Postgres knows it (e.g. 'America/Los_Angeles'), else a 422: never silently bucket in UTC."""
    if conn.execute("SELECT 1 FROM pg_timezone_names WHERE name = %s", (tz,)).fetchone() is None:
        raise HTTPException(422, [f"unknown time zone: {tz!r}"])
    return tz


def bucket_seconds(bucket_minutes, since, until):
    """Bucket size in seconds; refuses sizes that would make an enormous chart."""
    end = until or datetime.now(timezone.utc)
    if (end - since).total_seconds() / (bucket_minutes * 60) > MAX_BUCKETS:
        raise HTTPException(422, [f"bucket_minutes is too small for this range (over {MAX_BUCKETS} buckets)"])
    return bucket_minutes * 60


MAX_BUCKETS = 2000


def laminar_links(conn):
    """A function from a Laminar trace ID to its page in Laminar (None when the project ID isn't set)."""
    ui_url, project_id = conn.execute(
        "SELECT laminar_ui_url, laminar_project_id FROM projects ORDER BY id LIMIT 1").fetchone()
    if project_id is None:
        return lambda trace_id: None
    base = f"{ui_url.rstrip('/')}/project/{project_id}/traces/"
    return lambda trace_id: base + trace_id


def require_signal(conn, signal_id):
    if conn.execute("SELECT 1 FROM signals WHERE id = %s", (signal_id,)).fetchone() is None:
        raise HTTPException(404, "signal not found")


def rows(conn, sql, params):
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()


@app.get("/api/signals", response_model=list[SignalListItem])
def list_signals(since: datetime | None = None, until: datetime | None = None, conn=Depends(get_conn)):
    params = {"project": the_project(conn), **time_range(since, until)}
    end = params["until"] or datetime.now(timezone.utc)
    activity = {}
    for r in rows(conn, ACTIVITY_SQL, {**params, "end": end, "slices": ACTIVITY_SLICES}):
        activity.setdefault(r["signal_id"], [0] * ACTIVITY_SLICES)[r["slice"] - 1] = r["events"]
    return [SignalListItem(**r, activity=activity.get(r["id"], [0] * ACTIVITY_SLICES))
            for r in rows(conn, SIGNALS_SQL, params)]


@app.get("/api/signals/{signal_id}/events", response_model=EventPage)
def list_events(signal_id: int, since: datetime | None = None, until: datetime | None = None,
                severity: Literal["info", "warning", "critical"] | None = None,
                cluster: int | None = None, q: str | None = None,
                offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=MAX_LIMIT),
                conn=Depends(get_conn)):
    require_signal(conn, signal_id)
    link = laminar_links(conn)
    params = {"signal": signal_id, "severity": severity, "cluster": cluster, **search(q),
              **time_range(since, until)}
    total = conn.execute("SELECT count(*) " + EVENTS_FROM, params).fetchone()[0]
    page = rows(conn, EVENT_COLUMNS + EVENTS_FROM +
                " ORDER BY t.ended_at DESC, e.id DESC LIMIT %(limit)s OFFSET %(offset)s",
                {**params, "limit": limit, "offset": offset})
    return EventPage(total=total, events=[EventOut(**r, laminar_url=link(r["laminar_trace_id"]))
                                          for r in page])


@app.get("/api/signals/{signal_id}/event-counts", response_model=list[EventCount])
def count_events(signal_id: int, bucket_minutes: int = Query(ge=1), tz: str = "UTC",
                 since: datetime | None = None, until: datetime | None = None,
                 severity: Literal["info", "warning", "critical"] | None = None,
                 cluster: int | None = None, q: str | None = None, conn=Depends(get_conn)):
    """Events per time bucket and cluster, with the same filters as the events list."""
    require_signal(conn, signal_id)
    params = {"signal": signal_id, "severity": severity, "cluster": cluster, **search(q),
              **time_range(since, until)}
    params["bucket_seconds"] = bucket_seconds(bucket_minutes, params["since"], params["until"])
    params["tz"] = known_zone(conn, tz)
    return [EventCount(**r) for r in rows(
        conn, f"SELECT {BUCKET} AS bucket, cm.cluster_id, count(*) AS events " + EVENTS_FROM +
        " GROUP BY 1, 2 ORDER BY 1, 2", params)]


@app.get("/api/signals/{signal_id}/runs", response_model=RunPage)
def list_runs(signal_id: int, since: datetime | None = None, until: datetime | None = None,
              status: Literal["pending", "running", "done", "failed"] | None = None,
              source: Literal["live", "backfill"] | None = None,
              outcome: Literal["event", "none"] | None = None, q: str | None = None,
              offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=MAX_LIMIT),
              conn=Depends(get_conn)):
    require_signal(conn, signal_id)
    link = laminar_links(conn)
    params = {"signal": signal_id, "status": status, "source": source, "outcome": outcome, **search(q),
              **time_range(since, until)}
    total = conn.execute("SELECT count(*) " + RUNS_FROM, params).fetchone()[0]
    page = rows(conn, RUN_COLUMNS + RUNS_FROM +
                " ORDER BY t.ended_at DESC, r.id DESC LIMIT %(limit)s OFFSET %(offset)s",
                {**params, "limit": limit, "offset": offset})
    return RunPage(total=total, runs=[RunOut(**r, laminar_url=link(r["laminar_trace_id"])) for r in page])


@app.get("/api/signals/{signal_id}/run-counts", response_model=list[RunCount])
def count_runs(signal_id: int, bucket_minutes: int = Query(ge=1), tz: str = "UTC",
               since: datetime | None = None, until: datetime | None = None,
               status: Literal["pending", "running", "done", "failed"] | None = None,
               source: Literal["live", "backfill"] | None = None,
               outcome: Literal["event", "none"] | None = None, q: str | None = None,
               conn=Depends(get_conn)):
    """Finished runs per time bucket by outcome, with the same filters as the runs list."""
    require_signal(conn, signal_id)
    params = {"signal": signal_id, "status": status, "source": source, "outcome": outcome, **search(q),
              **time_range(since, until)}
    params["bucket_seconds"] = bucket_seconds(bucket_minutes, params["since"], params["until"])
    params["tz"] = known_zone(conn, tz)
    return [RunCount(**r) for r in rows(conn, f"""
        SELECT bucket, count(*) FILTER (WHERE status = 'done' AND found) AS event,
               count(*) FILTER (WHERE status = 'done' AND NOT found) AS nothing,
               count(*) FILTER (WHERE status = 'failed') AS failed
        FROM (SELECT {BUCKET} AS bucket, r.status,
                     EXISTS (SELECT 1 FROM signal_events e WHERE e.run_id = r.id) AS found
              {RUNS_FROM} AND r.status IN ('done', 'failed')) finished
        GROUP BY bucket ORDER BY bucket""", params)]


@app.get("/api/signals/{signal_id}/clusters", response_model=ClusterList)
def list_clusters(signal_id: int, since: datetime | None = None, until: datetime | None = None,
                  conn=Depends(get_conn)):
    """The signal's clusters that have events in the range, counted within the range."""
    require_signal(conn, signal_id)
    params = {"signal": signal_id, **time_range(since, until)}
    totals = rows(conn, CLUSTER_TOTALS_SQL, params)[0]
    return ClusterList(**totals, clusters=[ClusterOut(**r) for r in rows(conn, CLUSTERS_SQL, params)])


# ------------------------------------------------------------------ Home

class SignalCost(BaseModel):
    id: int
    name: str
    problem_events: int
    cost_usd: float               # finished runs with a known price
    unpriced_runs: int            # finished runs whose price is unknown (cost_usd NULL)


class ProblemCluster(BaseModel):
    id: int
    name: str | None
    signal_id: int
    signal_name: str
    problem_events: int


class SignalCount(BaseModel):
    bucket: datetime
    signal_id: int
    problem_events: int


class Overview(BaseModel):
    signals: list[SignalCost]
    problem_clusters: list[ProblemCluster]   # every cluster with a problem event in range, most first
    problem_counts: list[SignalCount]
    run_counts: list[RunCount]               # finished runs only: pending and running aren't counted


OVERVIEW_SIGNALS_SQL = """
SELECT s.id, s.name,
       (SELECT count(*) FROM signal_events e
        JOIN signal_versions v ON v.id = e.signal_version_id JOIN traces t ON t.id = e.trace_id
        WHERE v.signal_id = s.id AND e.severity IN ('warning', 'critical') AND {in_range})
           AS problem_events,
       coalesce(rc.cost_usd, 0) AS cost_usd, coalesce(rc.unpriced_runs, 0) AS unpriced_runs
FROM signals s
LEFT JOIN LATERAL (
    SELECT sum(r.cost_usd) AS cost_usd, count(*) FILTER (WHERE r.cost_usd IS NULL) AS unpriced_runs
    FROM signal_runs r
    JOIN signal_versions v ON v.id = r.signal_version_id JOIN traces t ON t.id = r.trace_id
    WHERE v.signal_id = s.id AND r.status IN ('done', 'failed') AND {in_range}
) rc ON true
WHERE s.project_id = %(project)s
ORDER BY s.created_at, s.id
"""

IN_RANGE = "t.ended_at >= %(since)s AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)"

PROBLEM_CLUSTERS_SQL = f"""
SELECT c.id, c.name, s.id AS signal_id, s.name AS signal_name, count(*) AS problem_events
FROM signal_events e
JOIN traces t ON t.id = e.trace_id
JOIN cluster_members cm ON cm.event_id = e.id
JOIN clusters c ON c.id = cm.cluster_id
JOIN signals s ON s.id = c.signal_id
WHERE s.project_id = %(project)s AND {leaf_cluster("c")} AND e.severity IN ('warning', 'critical')
  AND {IN_RANGE}
GROUP BY c.id, c.name, s.id, s.name
ORDER BY problem_events DESC, c.id
"""

PROBLEM_COUNTS_SQL = f"""
SELECT {BUCKET} AS bucket, v.signal_id, count(*) AS problem_events
FROM signal_events e
JOIN signal_versions v ON v.id = e.signal_version_id
JOIN signals s ON s.id = v.signal_id
JOIN traces t ON t.id = e.trace_id
WHERE s.project_id = %(project)s AND e.severity IN ('warning', 'critical') AND {IN_RANGE}
GROUP BY 1, 2 ORDER BY 1, 2
"""

ALL_RUN_COUNTS_SQL = f"""
SELECT bucket, count(*) FILTER (WHERE status = 'done' AND found) AS event,
       count(*) FILTER (WHERE status = 'done' AND NOT found) AS nothing,
       count(*) FILTER (WHERE status = 'failed') AS failed
FROM (SELECT {BUCKET} AS bucket, r.status,
             EXISTS (SELECT 1 FROM signal_events e WHERE e.run_id = r.id) AS found
      FROM signal_runs r
      JOIN signal_versions v ON v.id = r.signal_version_id
      JOIN signals s ON s.id = v.signal_id
      JOIN traces t ON t.id = r.trace_id
      WHERE s.project_id = %(project)s AND r.status IN ('done', 'failed') AND {IN_RANGE}) finished
GROUP BY bucket ORDER BY bucket
"""


@app.get("/api/overview", response_model=Overview)
def overview(bucket_minutes: int = Query(ge=1), tz: str = "UTC", since: datetime | None = None,
             until: datetime | None = None, conn=Depends(get_conn)):
    """Everything Home shows, counted in SQL."""
    params = {"project": the_project(conn), **time_range(since, until)}
    params["bucket_seconds"] = bucket_seconds(bucket_minutes, params["since"], params["until"])
    params["tz"] = known_zone(conn, tz)
    return Overview(
        signals=[SignalCost(**r) for r in rows(conn, OVERVIEW_SIGNALS_SQL.format(in_range=IN_RANGE), params)],
        problem_clusters=[ProblemCluster(**r) for r in rows(conn, PROBLEM_CLUSTERS_SQL, params)],
        problem_counts=[SignalCount(**r) for r in rows(conn, PROBLEM_COUNTS_SQL, params)],
        run_counts=[RunCount(**r) for r in rows(conn, ALL_RUN_COUNTS_SQL, params)],
    )


# ------------------------------------------------------------------ test on a sample trace

class TestRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    laminar_trace_id: str
    definition: SignalEdit


class FindingOut(BaseModel):
    severity: str
    fields: dict
    evidence_labels: list[str]
    span_ids: list[str]


class TestRunOut(BaseModel):
    findings: list[FindingOut]
    analysis: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float | None


TRACE_SQL = """
SELECT t.id, t.laminar_trace_id::text, t.session_id, t.started_at, t.ended_at, t.status,
       p.laminar_api_url
FROM traces t
JOIN projects p ON p.id = t.project_id
WHERE t.laminar_trace_id = %s
"""


def get_judge():
    """The judge to call. Tests replace this with a fake."""
    return judge


@app.post("/api/test-run", response_model=TestRunOut)
def test_run(body: TestRunIn, conn=Depends(get_conn), judge_fn=Depends(get_judge)):
    """Judge one stored trace with an unsaved definition. Writes nothing."""
    check(body.definition)
    try:
        trace_uuid = uuid.UUID(body.laminar_trace_id)
    except ValueError:
        raise HTTPException(422, ["laminar_trace_id must be a trace ID (a UUID)"])
    row = conn.execute(TRACE_SQL, (trace_uuid,)).fetchone()
    if row is None:
        raise HTTPException(404, "trace not found: only traces Tripwire has already stored can be tested")
    trace = TraceRow(id=row[0], laminar_trace_id=row[1], session_id=row[2], started_at=row[3],
                     ended_at=row[4], status=row[5])
    d = definition(body.definition)
    version = SignalVersion(id=None, signal_id=None, name=body.definition.name.strip(),
                            instruction=d["instruction"], critical_guidance=d["critical_guidance"],
                            output_schema=d["output_schema"], model=d["model"])
        # The judge's own client: deadline-bound, no retries, and the judge closes it.
    laminar = JudgeLaminarSQL(os.environ.get("LMNR_PROJECT_API_KEY", ""), row[6])
    try:
        result = judge_fn(laminar, trace, version)
    except JudgeError as e:
        raise HTTPException(503 if e.retryable else 422, f"the judge couldn't finish: {e}")
    u = result.usage
    return TestRunOut(
        findings=[FindingOut(severity=f.severity, fields=f.fields, evidence_labels=f.evidence_labels,
                             span_ids=f.span_ids) for f in result.findings],
        analysis=result.analysis, model=result.model, input_tokens=u.input_tokens,
        cached_input_tokens=u.cached_input_tokens, output_tokens=u.output_tokens, cost_usd=u.cost_usd)


# dev view: the developer page's endpoint (dev.py). To remove it, delete this block and dev.py.
from .dev import router as dev_router  # noqa: E402
app.include_router(dev_router)
