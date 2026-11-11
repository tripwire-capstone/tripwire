# Signals design (Signals pair)

*Draft, Tue Nov 10, 2026; updated Wed Nov 11. Decisions from the Signals pair's design sessions. Source of truth for the
signal runner and judge; when a decision changes, update this file in the same PR.*

For the Clusters pair, `FOR_CLUSTERING_PAIR.md` (same folder) answers their questions in short, and
`FOR_CLUSTERING_AGENTS.md` gives their coding assistants the details. The work is split in two
halves, each with its own handoff: `HANDOFF_JOB_FLOW.md` (poller fan-out, runner, migration, API) and
`HANDOFF_JUDGE.md` (LLM module, rendering, prompt, checks, templates). Read with:
`../../CONTRACT.md` (services, tables, job flow, conventions) and `../clustering/DESIGN.md`.

**Ground rule:** the schema is expected to change. Schema changes go in a new migration from the
owning pair, announced to the team. The Signals pair's changes so far are in migration 003
(`20261111120000_signal_runs_findings.sql`).

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
| S11 | **Output checks:** cited labels must exist in the render's label → span-ID map; unknown labels are dropped, and a finding left with no valid evidence is dropped (counted on the run as `dropped_findings`). A refusal fails the run. The one repair is for a cut-off answer (S19). | Uncited findings are the most common way judges invent problems. |
| S12 | **Earlier turns:** up to the last 5 traces with the same `session_id`, as plain user message + assistant reply pairs (from each trace's root span), **no tool calls and no span labels**, in an `<earlier_turns context_only="true">` block. No session ID → none. | Enough to see a visitor repeating themselves; unlabelled turns can't be cited. |
| S13 | **The prompt:** a fixed system message (review one turn; judge only the current turn; one finding per distinct problem, empty list if none; cite span labels; severity anchors: info = notable, not a problem; warning = the user got a worse result; critical = the user was harmed, misled or blocked; use only what the trace shows), then a user message: the rendered trace, then a `<signal>` block (name, instruction, optional "what counts as critical"). | Trace and rules identical for every signal; only the `<signal>` block varies. |
| S14 | **Test on a sample trace:** a synchronous API call with a trace ID and an unsaved draft definition; returns findings, analysis and cost. Same `judge()` as live runs; writes no runs or events, never clustered or alerted. | Small wording changes swing match rates several-fold (Raindrop), so trying a wording before saving is core. |
| S15 | **Debug storage:** every run stores `analysis` and the raw model output. A failed run keeps its rendered trace. A "found nothing" run can be re-rendered from Laminar on demand. | Small database; "why did it flag this?" and "why did it fail?" stay answerable. |
| S16 | **LLM provider: OpenAI** for now; every run records its model, token counts and dollar cost. | Already wired (`.env.example`, embeddings); strict JSON-schema output. |
| S17 | **Findings have no built-in text.** Any text is one of the signal's own fields (in `payload`); templates include a `description` field. `signal_events.description` is nullable, not dropped, because the clusterer still reads it. | Matches Laminar; removes a built-in field; nothing the clusterer reads breaks. |
| S18 | **Cost of every attempt is recorded.** A `Usage` (input, cached input and output tokens, cost) covers every model call inside one `judge()`, the repair included, and is carried by both `JudgeResult` and `JudgeError`. The runner adds each attempt's usage to the run row, failed attempts too. If any call's cost is unknown, the run's cost is NULL. | Otherwise spend on failed attempts and repairs is invisible; unknown beats wrong. |
| S19 | **Cut-off answers:** the first call allows 4,000 output tokens; if the answer is cut off (`finish_reason = "length"`), the same messages are sent once more with 8,000. That is the one repair. Cut off again → `JudgeError` that uses an attempt. | In strict mode a cut-off is practically the only bad output, and it needs more room, not an error message. |
| S20 | **`output_schema` format:** a list of fields `{name, type, description, choices, nullable}`; `type` is string, number, boolean or enum; `choices` only (and always) for enum; names match `^[a-zA-Z_][a-zA-Z0-9_]*$`, are unique, at least one field. The API validates it; the judge turns it into OpenAI's strict schema. | The mock UI's `payloadFields` shape; one format both halves share. |
| S21 | **Tool errors:** a tool's output is always rendered as-is. The marker `ERROR` (with the exception's type and message, or "(no message)") is added only when Laminar recorded the span as failed (`status = error` or an `exception` event). The prompt explains the marker. | Many tools return errors as data; guessing an app's conventions could mislead the judge. |
| S22 | **`judge_input`** = the rendered trace as the model read it: the agent's system prompt, up to 5 earlier turns, and the current turn with span labels, tool calls, results and `ERROR` markers. Not the judge's rules, the signal block, the schema or the answer. Stored on every event of a run (same text); on `signal_runs.rendered_trace` for failed runs; nowhere for found-nothing runs. Format changes are announced to the Clusters pair before merging. | The clusterer's summarizer and the UI's event panel both need "what the judge saw". |
| S23 | **Time limits inside `judge()`:** two Laminar queries at 10 s each on a judge-only client with no retries, one model call at 60 s with the OpenAI SDK's retries off, one repair at 60 s: about 140 s worst case. Timeouts become `JudgeError`s that don't use an attempt; only the runner retries. The runner refuses to start unless the lease is at least 2 × fetch + 2 × model + 30 s. | With default client settings one call could outlast the 180 s lease, and the run would be judged and paid for twice. |
| S24 | **Work split:** job flow (poller fan-out, runner, migrations, API) and judge (LLM module, rendering, prompt, checks, templates), meeting at `judge(laminar, trace, version) -> JudgeResult` in `tripwire/judge/types.py`. | The halves share no files; the stub judge lets the runner be built without an LLM. |
| S25 | **Evidence labels on events.** Each event stores the span labels its finding cited (`evidence_labels`, e.g. `["S3"]`), as they appear in `judge_input`, next to the same spans as real Laminar `span_ids`, in the same order. | Events of one run share `judge_input`; the labels show which part of it belongs to which event. |

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
| `JUDGE_FETCH_TIMEOUT_SECONDS` | 10 | S23 |
| `JUDGE_MODEL_TIMEOUT_SECONDS` | 60 | S23 |
| `OPENAI_API_KEY` | (required) | S16 |

## What other pairs read from us

| Table | Columns | Notes |
|---|---|---|
| `signal_events` | `id`, `run_id`, `finding_index`, `trace_id`, `signal_version_id`, `created_at`, `severity`, `payload`, `judge_input`, `evidence_labels`, `span_ids`, `description` | Up to 5 rows per run, numbered by `finding_index` (S6). `description` is nullable (S17). `judge_input` is the rendered trace (S22). `evidence_labels` point into `judge_input` (S25). |
| `signal_versions` | `signal_id`, `instruction`, `output_schema` | Current version = highest `version_number`. `output_schema` is a field list (S20). |
| `signal_runs` | `source` (`live` / `backfill`) | |
| `traces` | `session_id` | NULL when the app sends none. |

## Open items

- **Trace rendering:** which spans are shown and how (S-labels, LLM outputs only, tool calls and
  results); tool errors are settled (S21) and `judge_input`'s contents too (S22).
- **Size limit and trimming** for rendered traces: none for now; a too-long trace fails on the provider's
  context-length error.
- **Model and pricing:** default judge model, per-model prices; possibly an open-source library.
- **Shared LLM module** with the Clusters pair: structured output, token counts, four error types
  (service problem, bad output, input too long, refused), a test fake. Interface to agree with them.
- **Gold sets:** proving the judge works, possibly from tau-traffic's answer key.
