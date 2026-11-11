"""Tripwire's HTTP API: create, read, edit, and turn signals on and off.

Run: uvicorn tripwire.api.app:app --host 0.0.0.0 --port 8080
"""

import logging
import os
from datetime import datetime

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

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