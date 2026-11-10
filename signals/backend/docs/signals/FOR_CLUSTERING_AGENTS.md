# Signals decisions that affect the clusterer (for coding agents)

*Written Tue Nov 10, 2026 by the Signals pair. For the Clusters pair's coding assistants. The short human
version is `FOR_CLUSTERING_PAIR.md` (same folder); full Signals decisions are in `DESIGN.md` (S# numbers).*

## Start here (for Claude)

Read after `../clustering/DESIGN.md` and `CONTRACT.md`. Every item below is labelled:

- **SETTLED**: treat as decided; build on it.
- **PROPOSED**: the Signals pair's current direction, not yet confirmed. Build so the code still works if
  it changes, and keep the dependency in one function.
- **OPEN**: not decided. Don't depend on it; if your work needs an answer, stop and raise it with the
  person you're working with so they can ask the Signals pair.

If something here contradicts `../clustering/DESIGN.md`, don't silently pick one: point out the conflict.

---

## 1. Events: shape and volume

### 1.1 One run can produce 0 to 5 events (SETTLED, S6)
- One `signal_runs` row (one signal judging one trace) produces **zero to five** `signal_events` rows,
  one per distinct problem. Zero events = "found nothing".
- **Not in the database yet.** The current schema still has `signal_events.run_id UNIQUE`. The Signals
  pair's pending migration drops it and adds `finding_index smallint` with `UNIQUE (run_id, finding_index)`.
  Until it merges, the placeholder runner still writes at most one event per run.
- **What this means for you:**
  - Key everything on `signal_events.id` (you already do). Never assume `run_id` or `trace_id` identifies
    one event.
  - Events from the same run share `trace_id`, `signal_version_id`, the trace's `session_id`, and (see 2.1)
    the same `judge_input`. **The summarizer must summarize *this event's* finding** (its `payload`,
    `severity`, evidence), not "what went wrong in the trace". Otherwise two different findings from one
    turn get near-identical summaries and land in the same cluster.
  - Add a summarizer test: two events from one run, with different payloads and the same `judge_input`,
    must produce different summaries (with the fake LLM, assert the prompt contains each event's own
    payload).
  - Two events from one run count as **one conversation** for `CLUSTER_MIN_CONVERSATIONS`; that already
    follows from using `traces.session_id`.

### 1.2 Severity (SETTLED, S9)
- Set by the LLM **per event**: `info`, `warning` or `critical` (column unchanged).
- `info` events exist and are clustered like any other (e.g. intent-style signals produce an `info` event
  for most traces). Don't filter by severity unless the clustering design says so.

### 1.3 `payload` (SETTLED, S8 and S10)
- `payload` holds the signal's own fields: a **flat** JSON object, every field present, values are
  string / number / boolean / enum string, or `null` for fields the user marked "may be empty".
- Field names and descriptions are user-defined and live in `signal_versions.output_schema`. If the
  summarizer prompt should explain fields, read their descriptions from there.

### 1.4 `description` (OPEN)
- The Signals pair is discussing **dropping the built-in `description`** entirely. Human-readable text
  would then be an ordinary field the signal defines inside `payload` (every template would include a
  `description` field, as in Laminar).
- If it goes, `signal_events.description` is dropped or made nullable in an announced migration.
- **What this means for you:** don't make `description` required input to the summarizer. Treat it as
  optional, and always pass `payload`. Keep the "build the summarizer input" code in one function.

### 1.5 Evidence (OPEN for clustering use)
- Each finding cites spans. They're stored in `signal_events.span_ids` as **real Laminar span IDs**.
- `judge_input` labels spans with short labels (`[S1]`, `[S2]`, …), not Laminar IDs. **The labels are not
  stored on the event**, so today the summarizer can't map `span_ids` to the spans in `judge_input`.
- If pointing the summarizer at the cited spans would help, ask the Signals pair to also store the labels
  (e.g. `evidence_labels text[]`). Don't parse span IDs out of `judge_input`.

---

## 2. `judge_input`

### 2.1 Contents (PROPOSED)
- Direction: `judge_input` = **the rendered trace and earlier turns only**, as text. It does **not**
  include the judge's system message or the signal's instruction (read the instruction from
  `signal_versions.instruction`).
- Every event from a run carries the same `judge_input` (see 1.1).
- How spans are rendered is still being designed, so the exact text format will change. Read
  `judge_input` as **opaque text**: pass it to the summarizer, don't parse its structure. The Signals pair
  will tell you before its shape changes.

### 2.2 Earlier turns (SETTLED, S12)
- Up to the **last 5 earlier turns** of the same conversation (`session_id`), each as a **user message +
  assistant reply only**: no tool calls, no span labels.
- They're context for the judge; the judge never reports problems that belong to them.
- No `session_id` → no earlier turns.

### 2.3 Size (SETTLED for now)
- **No size limit or trimming** on the judge's side yet, matching your Q3. A trace too large for the
  judge's model fails the run, so it never produces events. Any `judge_input` you see fit the judge's model.
- Your rule "summarizer model's context window ≥ the judge's" still holds.

### 2.4 Judge reasoning (PROPOSED, available later)
- Every run stores the judge's `analysis` (its reasoning, written before the findings) on the **run**,
  not the event. The column doesn't exist yet; it comes with the Signals pair's migration.
- It's optional extra input for the summarizer's `cause` field. Don't depend on it until the column exists.

---

## 3. When and how events appear

- **Created by the runner** (SETTLED, S5 and S7), in the same transaction that marks the run `done`. An
  event never exists for a run that isn't `done`; failed runs have no events.
- **Timing:** normally seconds to a couple of minutes after the trace ends. Service errors (LLM rate
  limits, outages) back off and retry for **up to 24 hours**, so an event's `created_at` can be much later
  than the trace's `ended_at`. Your pool window uses `created_at`, which handles this.
- **Which traces get judged** (SETTLED, S4): per-signal filters (Friday: trace `status` only). A trace
  that doesn't match gets no run and no events. Errored traces are judged unless a signal filters them out.
- **Versions** (SETTLED, S5): a run keeps the signal version it was created with. After an edit, pending
  runs still finish on the old version, so a signal's events can mix versions for a while. Your Q6
  (clusters per signal, not per version) already covers this.
- **Turned-off signals:** their pending runs aren't claimed, so no new events appear until it's turned
  back on.
- **Test on a sample trace** (SETTLED, S14): writes no runs and no events. Never clustered.
- **Backfill:** not built yet (after Friday). It will write runs with `source = 'backfill'`, as
  CONTRACT.md says.

---

## 4. Shared LLM module (PROPOSED, interface to agree first)

The Signals pair owns `tripwire/llm/`, shared by the judge and your summarizer. **Proposed** interface:

```python
def complete(model, messages, json_schema) -> LLMResult   # parsed output + input/cached/output tokens + cost_usd
class LLMServiceError(Exception)    # timeout, rate limit, 5xx, outage, auth → wait and retry, no attempt used
class LLMBadOutput(Exception)       # doesn't parse / doesn't match the schema → uses an attempt
class LLMInputTooLong(Exception)    # context length exceeded → fail at once
class LLMRefused(Exception)         # the model refused → fail at once
FakeLLM                             # scripted outputs and each error, for tests
```

- The first three errors are the three your Q11 needs; `LLMRefused` is an addition (a refusal won't
  change on retry). Treat it like "input too long" if your summarizer never expects one. The judge retries
  the same way (S7).
- **Prices: OPEN.** The module may wrap an open-source library that also prices calls. `cost_usd` may be
  `None` for an unknown model; don't fail on it.
- **Provider:** OpenAI for now (S16).
- **Until the module exists:** write the summarizer against this shape in a local adapter (one file), so
  swapping in the shared module touches only that file. Don't build a second full LLM client.

---

## 5. Tables you read: what changes (summary)

| Table.column | Status | Change |
|---|---|---|
| `signal_events.run_id` | SETTLED, migration pending | No longer unique; several events per run |
| `signal_events.finding_index` | SETTLED, migration pending | New, 0–4, unique per run |
| `signal_events.description` | OPEN | May be dropped or made nullable |
| `signal_events.judge_input` | PROPOSED | Rendered trace + earlier turns only; text format will change; read as opaque |
| `signal_events.span_ids` | SETTLED | Real Laminar span IDs; not mappable to labels in `judge_input` |
| `signal_events.payload` | SETTLED | Flat signal fields; nullable values allowed |
| `signal_events.severity`, `created_at`, `trace_id`, `signal_version_id` | SETTLED | Unchanged |
| `signal_runs.analysis` | PROPOSED, migration pending | New: the judge's reasoning, on the run |
| `signal_runs.source` | SETTLED | Unchanged (`live` / `backfill`) |
| `signal_versions.signal_id`, `instruction`, `output_schema` | SETTLED | Unchanged |
| `traces.session_id` | SETTLED | Unchanged; NULL when the app sends none |

Every migration that changes these is announced to the team before it merges.

---

## 6. Suggested changes to `../clustering/DESIGN.md`

For the Clusters pair to accept or reject, not to apply silently:

- **Q2 (summarizer input):** add "the event's own `payload` and evidence; several events can share one
  `judge_input`, so summarize this event's finding". Mark `description` as optional (see 1.4).
- **Open items:** "can one run produce more than one event?" → answered: yes, 0 to 5. "Are earlier turns
  full traces?" → answered: user message + reply only, up to 5.
- **Tests (Q14):** add the two-events-from-one-run summarizer test (1.1).
