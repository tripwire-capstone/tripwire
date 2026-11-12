"""Tripwire's HTTP API: signals (create, read, edit, on/off), their events and runs, and test-on-trace.

Run: uvicorn tripwire.api.app:app --host 0.0.0.0 --port 8080
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from ..judge.judge import judge
from ..judge.types import JudgeError, SignalVersion, TraceRow
from ..poller.laminar import LaminarSQL
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


def definition(body):
    """The parts of a signal that make up a version, in their stored form."""
    return {"instruction": body.instruction.strip(),
            "output_schema": normalize_fields(body.output_schema),
            "filters": body.filters,
            "critical_guidance": body.critical_guidance.strip() if body.critical_guidance else None,
            "model": body.model or os.environ.get("JUDGE_DEFAULT_MODEL", "stub")}


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


def current(conn, signal_id):
    row = conn.cursor(row_factory=dict_row).execute(CURRENT_SQL, (signal_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "signal not found")
    return SignalOut(**row)


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
    try:
        with conn.transaction():
            signal_id = conn.execute(
                "INSERT INTO signals (project_id, name, template) VALUES (%s, %s, %s) RETURNING id",
                (project_id, name, body.template)).fetchone()[0]
            conn.execute(INSERT_VERSION_SQL, stored({"signal_id": signal_id, "number": 1, **definition(body)}))
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
    new = definition(body)
    try:
        with conn.transaction():
            # Lock the signal so two edits at once take turns instead of both making "version N+1".
            if conn.execute("SELECT 1 FROM signals WHERE id = %s FOR UPDATE", (signal_id,)).fetchone() is None:
                raise HTTPException(404, "signal not found")
            now = current(conn, signal_id)
            if name != now.name:
                conn.execute("UPDATE signals SET name = %s WHERE id = %s", (name, signal_id))
            old = {key: getattr(now, key) for key in new}
            if new != old:
                conn.execute(INSERT_VERSION_SQL, stored({"signal_id": signal_id,
                                                         "number": now.version_number + 1, **new}))
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


# Counts are by when the turn happened (trace end), so backfilled runs land on their trace's day.
SIGNALS_SQL = """
SELECT s.id, s.name, s.active, s.template, s.created_at, v.version_number,
       coalesce(ru.runs, 0) AS runs, coalesce(ev.events, 0) AS events,
       coalesce(ev.problem_events, 0) AS problem_events, ev.last_event_at
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
           max(e.created_at) AS last_event_at
    FROM signal_events e
    JOIN signal_versions sv ON sv.id = e.signal_version_id
    JOIN traces t ON t.id = e.trace_id
    WHERE sv.signal_id = s.id AND t.ended_at >= %(since)s
      AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
) ev ON true
WHERE s.project_id = %(project)s
ORDER BY s.created_at, s.id
"""

EVENTS_SQL = """
SELECT e.id, e.run_id, e.finding_index, e.severity, e.payload, e.evidence_labels, e.span_ids,
       v.version_number, r.source, t.laminar_trace_id::text AS laminar_trace_id, t.session_id,
       t.ended_at AS trace_ended_at, cm.cluster_id, e.created_at
FROM signal_events e
JOIN signal_versions v ON v.id = e.signal_version_id
JOIN signal_runs r ON r.id = e.run_id
JOIN traces t ON t.id = e.trace_id
LEFT JOIN cluster_members cm ON cm.event_id = e.id
WHERE v.signal_id = %(signal)s AND t.ended_at >= %(since)s
  AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
ORDER BY t.ended_at DESC, e.id DESC
LIMIT %(limit)s
"""

RUNS_SQL = """
SELECT r.id, r.status, r.source, r.attempts,
       (SELECT count(*) FROM signal_events e WHERE e.run_id = r.id) AS events,
       r.model, r.cost_usd, r.last_error, v.version_number,
       t.laminar_trace_id::text AS laminar_trace_id, t.ended_at AS trace_ended_at,
       r.created_at, r.finished_at
FROM signal_runs r
JOIN signal_versions v ON v.id = r.signal_version_id
JOIN traces t ON t.id = r.trace_id
WHERE v.signal_id = %(signal)s AND t.ended_at >= %(since)s
  AND (%(until)s::timestamptz IS NULL OR t.ended_at < %(until)s)
ORDER BY t.ended_at DESC, r.id DESC
LIMIT %(limit)s
"""


def time_range(since, until):
    return {"since": since or datetime.now(timezone.utc) - DEFAULT_RANGE, "until": until}


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


@app.get("/api/signals", response_model=list[SignalSummary])
def list_signals(since: datetime | None = None, until: datetime | None = None, conn=Depends(get_conn)):
    rows = conn.cursor(row_factory=dict_row).execute(
        SIGNALS_SQL, {"project": the_project(conn), **time_range(since, until)}).fetchall()
    return [SignalSummary(**row) for row in rows]


@app.get("/api/signals/{signal_id}/events", response_model=list[EventOut])
def list_events(signal_id: int, since: datetime | None = None, until: datetime | None = None,
                limit: int = Query(100, ge=1, le=MAX_LIMIT), conn=Depends(get_conn)):
    require_signal(conn, signal_id)
    link = laminar_links(conn)
    rows = conn.cursor(row_factory=dict_row).execute(
        EVENTS_SQL, {"signal": signal_id, "limit": limit, **time_range(since, until)}).fetchall()
    return [EventOut(**row, laminar_url=link(row["laminar_trace_id"])) for row in rows]


@app.get("/api/signals/{signal_id}/runs", response_model=list[RunOut])
def list_runs(signal_id: int, since: datetime | None = None, until: datetime | None = None,
              limit: int = Query(100, ge=1, le=MAX_LIMIT), conn=Depends(get_conn)):
    require_signal(conn, signal_id)
    link = laminar_links(conn)
    rows = conn.cursor(row_factory=dict_row).execute(
        RUNS_SQL, {"signal": signal_id, "limit": limit, **time_range(since, until)}).fetchall()
    return [RunOut(**row, laminar_url=link(row["laminar_trace_id"])) for row in rows]


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
    laminar = LaminarSQL(os.environ.get("LMNR_PROJECT_API_KEY", ""), row[6],
                         timeout=int(os.environ.get("JUDGE_FETCH_TIMEOUT_SECONDS", 10)), retries=0)
    try:
        result = judge_fn(laminar, trace, version)
    except JudgeError as e:
        raise HTTPException(503 if e.retryable else 422, f"the judge couldn't finish: {e}")
    finally:
        laminar.close()
    u = result.usage
    return TestRunOut(
        findings=[FindingOut(severity=f.severity, fields=f.fields, evidence_labels=f.evidence_labels,
                             span_ids=f.span_ids) for f in result.findings],
        analysis=result.analysis, model=result.model, input_tokens=u.input_tokens,
        cached_input_tokens=u.cached_input_tokens, output_tokens=u.output_tokens, cost_usd=u.cost_usd)