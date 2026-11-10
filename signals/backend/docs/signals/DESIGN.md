# Signals design (Signals pair)

*Draft, Tue Nov 10, 2026. Decisions from the Signals pair's design session. Source of truth for the
signal runner and judge; when a decision changes, update this file in the same PR.*

For the Clusters pair, `FOR_CLUSTERING_PAIR.md` (same folder) answers their questions in short, and
`FOR_CLUSTERING_AGENTS.md` gives their coding assistants the details. Read with:
`../../CONTRACT.md` (services, tables, job flow, conventions) and `../clustering/DESIGN.md`.

**Ground rule:** the schema is expected to change. Schema changes go in a new migration from the
owning pair, announced to the team (the migration for S6 and S8 is still being discussed; see Open items).

---

## Pipeline

```
Laminar ──► poller ──► traces ──┬─► signal_runs (pending) ──► runner ──► judge() ──► signal_runs (done | failed)
         (every 10 s)           │    one per active signal                              + 0..5 signal_events
                                └─ same transaction ─┘   whose filters match
```

- **poller** (exactly one copy): stores new finished traces and creates their runs (S5).
- **runner** (one process, 4 worker threads): claims runs, calls `judge()`, writes the outcome (S7).
- **judge()**: reads the trace from Laminar, renders it, calls the LLM, checks the output. Writes
  nothing to the database; the caller stores the result, so "test on a sample trace" reuses it (S14).

## Decisions

| # | Decision | Why (short) |
|---|---|---|
| S1 | **Friday (Nov 13):** live judging on `docker compose` for custom signals and two templates (Failure, Friction), test on a sample trace, and the run and event data behind the Events and Runs pages. Backfill, versions UI, notifications, login and AWS come after. | Smallest slice that proves traces become events; backfill reuses the same job table (`source = 'backfill'`). |
| S2 | **One run judges one trace** (one turn) after its root span ends. Earlier turns of the same `session_id` are context only, never judged. | Laminar has no "conversation ended" marker; the poller already yields one row per trace. Counts read "N events in M conversations". |
| S3 | **One LLM call per (signal, trace).** No combined calls, no trained classifiers. | Each signal can be versioned, tested and given its own model on its own. |
| S4 | **Per-signal filters,** all must match, checked when runs are created. A trace that doesn't match gets **no run row** and costs nothing. **Friday: `status` only;** `span_name` and `min_tokens` later. | Cheap pre-check before paying for the LLM (Raindrop's main cost lesson, Laminar's filter design). |
| S5 | **The poller creates runs,** in the same transaction that stores the trace: one `pending` run per new trace × active signal whose current version's filters match, `ON CONFLICT DO NOTHING`. Filters are checked by one shared function, `matches(filters, trace)`, reused by backfill and the signal form. The poller reads Laminar's `traces` table (status, session ID, span names, token totals per trace). | Atomic: a stored trace can never be left without runs. A signal created later gets no runs for earlier traces (that's backfill); a run keeps the version it was created with; runs of a turned-off signal stay pending and aren't claimed. |
| S6 | **0 to 5 findings per run,** one per distinct problem; an empty list means "found nothing". Each finding is its own `signal_events` row. Over 5: keep the 5 most severe. | A turn with a tool error and a made-up fact is two problems; one blended event clusters badly. Laminar also allows several events per run. |
| S7 | **Run states:** `pending → running → done \| failed`, claimed one run at a time with `FOR UPDATE SKIP LOCKED` and a 3-minute lease (SQL in CONTRACT.md §4). At most 3 real attempts. **Service errors** (OpenAI 429, timeout, 5xx; Laminar unavailable) give the attempt back and set `not_before` with exponential backoff and jitter (capped at 5 min, honouring `retry-after`). **Bad output** after the repair retry, and an **expired lease**, use an attempt. A **refusal** fails at once. A run still pending 24 h after creation fails. Each run's cost is calculated on its own (no prompt caching for now). | An LLM outage would otherwise burn every run's attempts. Two 60 s calls plus a span fetch fit in 3 minutes, so no heartbeat. ~2 runs/minute needs one process. |
| S8 | **A signal definition:** name, instruction, output fields (flat, all required, string / number / boolean / enum; a field may be marked "may be empty", making it nullable), filters (S4), model. Severity is built into every finding, not a user field. | Flat required fields stay filterable and sortable (as in Laminar) and are what OpenAI's strict mode accepts. |
| S9 | **Severity is set by the LLM per finding:** info, warning or critical, guided by optional "what counts as critical" text on the signal. | The "problem = warning or critical" dashboard rule and critical-event alerts need it. |
| S10 | **Output format:** a strict JSON schema built per signal: `analysis` (reasoning, written first, stored on the run, never an event), then `findings`, each with `severity`, `evidence` (span labels) and `fields`. `fields` is generated from the version's output fields, with each field's description in the schema. `fields` is stored as `signal_events.payload`. | Strict output removes parsing failures; reasoning before verdict improves accuracy; nesting user fields avoids name clashes with built-ins. |
| S11 | **Output checks:** cited labels must exist in the render's label → span-ID map; unknown labels are dropped, and a finding left with no valid evidence is dropped (counted on the run). One repair retry for invalid output, then the run fails. A refusal or cut-off response fails the run. | Uncited findings are the most common way judges invent problems. |
| S12 | **Earlier turns:** up to the last 5 traces with the same `session_id`, as plain user message + assistant reply pairs (from each trace's root span), **no tool calls and no span labels**, in an `<earlier_turns context_only="true">` block. No session ID → none. | Enough to see a visitor repeating themselves; unlabelled turns can't be cited. |
| S13 | **The prompt:** a fixed system message (review one turn; judge only the current turn; one finding per distinct problem, empty list if none; cite span labels; severity anchors: info = notable, not a problem; warning = the user got a worse result; critical = the user was harmed, misled or blocked; use only what the trace shows), then a user message: the rendered trace, then a `<signal>` block (name, instruction, optional "what counts as critical"). | Trace and rules identical for every signal; only the `<signal>` block varies. |
| S14 | **Test on a sample trace:** a synchronous API call with a trace ID and an unsaved draft definition; returns findings, analysis and cost. Same `judge()` as live runs; writes no runs or events, never clustered or alerted. | Small wording changes swing match rates several-fold (Raindrop), so trying a wording before saving is core. |
| S15 | **Debug storage:** every run stores `analysis` and the raw model output. A failed run keeps its rendered trace. A "found nothing" run can be re-rendered from Laminar on demand. | Small database; "why did it flag this?" and "why did it fail?" stay answerable. |
| S16 | **LLM provider: OpenAI** for now; every run records its model, token counts and dollar cost. | Already wired (`.env.example`, embeddings); strict JSON-schema output. |

## Settings (env vars; names proposed)

| Variable | Default | Decision |
|---|---|---|
| `POLLER_INTERVAL_SECONDS` | 10 | existing |
| `RUNNER_THREADS` | 4 | S7 |
| `RUNNER_LEASE_SECONDS` | 180 | S7 |
| `RUNNER_MAX_ATTEMPTS` | 3 | S7 |
| `RUNNER_MAX_BACKOFF_SECONDS` | 300 | S7 |
| `RUNNER_GIVE_UP_HOURS` | 24 | S7 |
| `JUDGE_MAX_FINDINGS` | 5 | S6 |
| `JUDGE_EARLIER_TURNS` | 5 | S12 |
| `OPENAI_API_KEY` | (required) | S16 |

## What other pairs read from us

| Table | Columns | Notes |
|---|---|---|
| `signal_events` | `id`, `run_id`, `trace_id`, `signal_version_id`, `created_at`, `severity`, `payload`, `judge_input`, `description` | Several rows per run (S6). `description` and `judge_input` are still open (below). |
| `signal_versions` | `signal_id`, `instruction` | Current version = highest `version_number`. |
| `signal_runs` | `source` (`live` / `backfill`) | |
| `traces` | `session_id` | NULL when the app sends none. |

## Open items

- **`description` on events:** keep a built-in text field per finding, or drop it and rely on the
  signal's own fields (templates would include a `description` field)? The Clusters pair reads it today.
- **`judge_input`:** exactly what it holds and its shape (the Clusters pair asked for the rendered trace
  and earlier turns only, not the judge's instructions). To revisit with trace rendering.
- **Trace rendering:** which spans are shown and how (S-labels, LLM outputs only, tool calls and results,
  errors from span `exception` events).
- **Size limit and trimming** for rendered traces: none for now; a too-long trace fails on the provider's
  context-length error.
- **Model and pricing:** default judge model, per-model prices; possibly an open-source library.
- **Shared LLM module** with the Clusters pair: structured output, token counts, three error types
  (service problem, bad output, input too long), a test fake.
- **Migration:** several events per run (drop `UNIQUE (run_id)`, add `finding_index`) and
  `signal_runs.not_before`.
- **Gold sets:** proving the judge works, possibly from tau-traffic's answer key.
- **Work split** within the Signals pair.
