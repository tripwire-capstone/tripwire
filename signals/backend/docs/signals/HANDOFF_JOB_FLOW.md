# Handoff: the job-flow half of Signals (poller fan-out, runner, migration, API)

*Written Tue Nov 10, 2026. For whichever Signals-pair member builds this half ("A"), and their Claude
session. The other half (the judge, "B") is built in parallel; see `HANDOFF_JUDGE.md`.*

## Start here (for Claude)

Read, in order:
1. `DESIGN.md` (same folder): every Signals decision (S1–S16). **Treat SETTLED items as decided.** If
   something looks wrong, raise it with the person you're working with; don't silently design around it.
2. `../../CONTRACT.md`: services, table owners, job flow SQL (§4), conventions (§6).
3. `../../migrations/20261109120000_initial_schema.sql`, `tripwire/poller/`, `tripwire/runner/`
   (placeholder), `tripwire/placeholder.py`, `tests/conftest.py`, `tests/test_poll.py`.
4. `FOR_CLUSTERING_AGENTS.md`: what the Clusters pair reads from our tables. Don't break it.

Then propose the first PR (A2, below) before writing code. Walk the person you're working with through
the core logic of each piece before writing it.

**Labels used below:** **SETTLED** (decided), **PROPOSED** (current direction; confirm with the team
before relying on it in a way that's hard to change), **OPEN** (don't decide it yourself: stop and ask).

## Scope

Everything that moves a trace from Laminar to stored runs and events, and the API on top. You never call
an LLM: until the judge half lands, you use a stub `judge()` with the real signature.

| PR | Piece | Decisions |
|---|---|---|
| A2 | **Interface first:** `tripwire/judge/types.py` + stub `judge()`. Both Signals members review; merges before anything builds on it | PROPOSED (below) |
| A1 | **Migration 002**: several events per run, run-level debug columns, `not_before`, critical guidance. Announce to the team before merging | S6, S7, S9, S15 |
| A3 | **Poller fan-out:** read Laminar's `traces` table; create runs in the same transaction as the trace | S4, S5 |
| A4 | **Runner:** claim with lease, call `judge()`, write run + events, retries, sweep, threads | S7 |
| A5 | **API:** signals (create, edit = new version), runs list, events list, test on a sample trace | S8, S14 |
| A6 | **Wire mock UI pages** (Signals list, Events, Runs) to the API, if the team agrees | PROPOSED |

Order: A2 first (an hour), then A1, then A3 and A4 (A4 can start on the placeholder trace data), then A5.

## A2: the interface between the halves (PROPOSED: both members approve)

`tripwire/judge/types.py`. Field names here are what both halves build against; after A2 merges, any
change goes in its own small PR approved by both members.

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

@dataclass
class TraceRow:                  # what the runner passes in (from our `traces` table)
    id: int
    laminar_trace_id: str
    session_id: str | None
    started_at: datetime
    ended_at: datetime
    status: Literal["success", "error"]

@dataclass
class SignalVersion:             # a saved version, or an unsaved draft for test-on-trace (id = None)
    id: int | None
    signal_id: int | None
    name: str
    instruction: str
    critical_guidance: str | None
    output_schema: dict          # the signal's fields (S8)
    model: str

@dataclass
class Finding:
    severity: Literal["info", "warning", "critical"]
    span_ids: list[str]          # real Laminar span IDs (the judge maps its labels back)
    fields: dict                 # → signal_events.payload
    # description: str           # OPEN (Q25b): only if the team keeps a built-in description

@dataclass
class JudgeResult:
    findings: list[Finding]      # 0..5; empty = found nothing
    analysis: str                # → signal_runs.analysis
    raw_output: str              # → signal_runs.raw_output
    judge_input: str             # → signal_events.judge_input (same text on every event of the run)
    dropped_findings: int        # removed by the evidence check
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float | None       # None when the model's price is unknown

class JudgeError(Exception):
    def __init__(self, message, *, retryable: bool, uses_attempt: bool,
                 rendered_trace: str | None = None): ...
```

Stub, in `tripwire/judge/judge.py` (B replaces the body later; the signature never changes):

```python
def judge(laminar, trace: TraceRow, version: SignalVersion) -> JudgeResult:
    # canned 0–2 findings chosen with placeholder.picked() on the trace ID, zero tokens, cost 0
```

Give the stub a way to raise each kind of `JudgeError` in tests (e.g. a module-level hook tests can set).

## A1: migration 002 (SETTLED content, OPEN on `description`)

New file `migrations/<timestamp>_signal_runs_and_events.sql`:

- **`signal_events`:**
  - drop `UNIQUE (run_id)`;
  - add `finding_index smallint NOT NULL DEFAULT 0 CHECK (finding_index BETWEEN 0 AND 4)` and
    `UNIQUE (run_id, finding_index)` (S6);
  - `description`: **OPEN (Q25b)**, drop it, make it nullable, or keep it. Stop and ask; the Clusters
    pair reads this column, so whatever changes is announced first.
- **`signal_runs`:**
  - `not_before timestamptz` (S7);
  - `analysis text`, `raw_output text` (S15, every run);
  - `rendered_trace text` (S15, failed runs only);
  - `dropped_findings integer NOT NULL DEFAULT 0` (S11);
  - drop `skipped` from the status CHECK (S4: non-matching traces get no row; no sampling yet);
  - update the claim index to cover `not_before` if it helps the claim query.
- **`signal_versions`:** `critical_guidance text` (S9, optional "what counts as critical").

Remember the `db` container only runs migrations on an empty volume (`docker compose down -v`). A
migration runner is a separate CONTRACT.md open item; don't build it inside this PR.

## A3: poller fan-out (SETTLED, S4 and S5)

- **Read traces from Laminar's `traces` table** instead of root spans. In Laminar v0.3.3 it has `id`,
  `session_id`, `start_time`, `end_time`, `status`, `span_names`, `total_tokens` (and more). Keep a
  `start_time` bound in the WHERE clause: Laminar's SQL rewriter uses it to narrow the time range. Keep
  the settling delay and overlap. **Check first** (a test against real Laminar or a fixture) that it
  returns the same set of finished traces as the current root-span query; if it doesn't, keep the
  spans query and raise it.
- **Fan-out in the same transaction** as the trace insert: load active signals with their current
  version (highest `version_number`) once per poll; for each **newly inserted** trace × signal where
  `matches(version.filters, trace)` is true, insert a `pending` run, `ON CONFLICT DO NOTHING`.
- **`matches(filters, trace)`** in its own module (e.g. `tripwire/filters.py`), pure and unit-tested.
  Friday supports **only `{"field": "status", "op": "eq", "value": "error" | "success"}`** and an empty
  list (= match everything). Unknown filter types: raise, don't ignore. Backfill and the API reuse it.
- Store nothing new on `traces` for Friday (`span_name` and `min_tokens` filters come later).

## A4: runner (SETTLED, S7)

- **One process, `RUNNER_THREADS` (4) worker threads,** each with its own database connection, each
  claiming **one run at a time**.
- **Claim** (CONTRACT.md §4 SQL, plus): `AND (not_before IS NULL OR not_before <= now())` and only runs
  whose signal is `active`. Live before backfill, oldest first. Lease `RUNNER_LEASE_SECONDS` (180).
- **Never hold a transaction open during the judge call.** Claim → commit → load trace + version →
  `judge()` → finish in a new transaction.
- **Finish (success):** one transaction: update the run to `done` (guarded by `status = 'running' AND
  attempts = <claimed attempts>`; 0 rows → roll back, someone else owns it) with model, tokens, cost,
  `analysis`, `raw_output`, `dropped_findings`; insert one `signal_events` row per finding with
  `finding_index` 0..n-1, `payload = fields`, `span_ids`, `evidence_labels`, `judge_input`.
- **Finish (`JudgeError`):**

  | `retryable` | `uses_attempt` | Action |
  |---|---|---|
  | true | false | back to `pending`, attempt given back (`attempts - 1`), `not_before = now() + backoff`, `last_error` set |
  | true | true | back to `pending` with backoff, or `failed` if this was attempt `RUNNER_MAX_ATTEMPTS` (3) |
  | false | – | `failed` at once; store `rendered_trace` if given |

  Backoff: exponential with jitter, from 5 s, capped at `RUNNER_MAX_BACKOFF_SECONDS` (300); honour a
  `retry-after` if the error carries one.
- **Sweep** every loop: expired leases on the last attempt → `failed`; runs still not finished
  `RUNNER_GIVE_UP_HOURS` (24) after `created_at` → `failed` ("gave up after 24 h").
- **Graceful stop** (SIGTERM): stop claiming, let in-flight judge calls finish or let their leases
  expire.

## A5: API (SETTLED behaviour, PROPOSED framework)

- **FastAPI** (infra decision: FastAPI also serves the built frontend later). New `tripwire/api/`.
- **Signals:** list, get, create, edit. **Create** = `signals` row + version 1 in one transaction.
  **Edit** = a new `signal_versions` row with the full definition; never update a version in place.
  Turn on/off = `signals.active`.
- **Validate output fields** (S8): flat; types string / number / boolean / enum; names match
  `^[a-zA-Z_][a-zA-Z0-9_]*$`; optional "may be empty" flag. If Q25b drops `description`: at least one
  field required.
- **Runs list** and **events list** per signal, with a time range; shape them like the mock UI's data
  helpers (`signals/mock-ui/src/components/StoryClock.jsx`, `src/mock/data.js`). Include the "Open in
  Laminar" link (`projects.laminar_ui_url` + `laminar_project_id` + trace ID).
- **Test on a sample trace** (S14): `POST` with a Laminar trace ID and an unsaved definition → build a
  `SignalVersion(id=None, …)`, call `judge()` synchronously, return findings, analysis and cost.
  **Writes nothing.**
- **Templates:** the judge half provides them (`HANDOFF_JUDGE.md`, B5); the API only lists them.

## Tests this half needs

- `matches()`: empty filters, status match and mismatch, unknown filter type raises.
- Fan-out: a new trace gets one run per matching active signal; a re-read trace gets none; an inactive
  signal gets none; a signal created after the trace gets none.
- Claim: two threads never get the same run; `not_before` in the future isn't claimed; an expired lease
  is reclaimed and counts as an attempt.
- Finish: the late runner whose lease expired rolls back; 0, 1 and 5 findings write the right rows in one
  transaction; each `JudgeError` row of the table above.
- Sweep: final-attempt expiry and the 24 h give-up.
- API: edit creates version 2 and leaves version 1 untouched; invalid fields rejected; test-on-trace
  writes no rows.

## Open items (stop and ask; don't decide)

- **Q25b `description`:** affects A1 and A2.
- **Model and pricing** (`JUDGE_DEFAULT_MODEL`, prices): the judge half owns it; `cost_usd` may be `None`.
- **Backfill:** after Friday; keep `source` and the claim ordering ready for it.
- **Who builds the cluster API and view:** team question (see the clustering `DESIGN.md`).

## Not in scope

The LLM module, rendering, prompts, output checks, templates, gold sets (judge half). Clustering tables
(Clusters pair).

## Working agreements

- Branch + PR for everything; the other Signals-pair member reviews.
- Plain SQL migrations named `YYYYMMDDHHMMSS_what.sql`; never edit a merged one; announce schema changes.
- Settings only from env vars (add new ones to `.env.example`).
- Don't edit `tripwire/judge/` beyond A2's stub, or `tripwire/llm/`; agree interface changes first.
- A decision that changes goes into `DESIGN.md` in the same PR.
