# Clustering design (Clusters pair)

*Agreed Tue Nov 10, 2026, in a design session by both members of the Clusters pair. Source of truth for
both halves of the clusterer; the history and full reasoning live in the "Tripwire Clustering: Design
Notes" doc. When a decision changes, update this file in the same PR.*

For the Signals pair, `FOR_SIGNALS_PAIR.md` (same folder) summarizes what affects them and what we need
from them; their answers are in `../signals/FOR_CLUSTERING_AGENTS.md`. Read with: `../../CONTRACT.md` (services, tables, job flow, conventions) and the team's "Tripwire Backend:
Design Decisions to Settle" doc (decisions referred to as #n).

**Ground rule:** the schema is expected to change. Don't work around current tables or constraints;
propose schema changes when a design needs them (new migration, announced to the team).

**Supersedes** older notes that say the Signals pair's event `description` is what gets clustered, and
any notes describing the Phase 2 prototype's summarizer as the plan. The prototype is a source of
lessons and portable code, not the design.

---

## Pipeline

```
signal_events ──► summarize ──► event_summaries ──► embed ──► event_embeddings ──► assign ──► cluster_members
   (Signals pair)   (LLM)                            (API)                       └─► pool ──► group ──► new clusters ──► name
```

One `clusterer` process (exactly one copy, per CONTRACT.md). Each pass, in order:

1. **Prepare** up to `CLUSTER_PREPARE_BATCH` new events (default 50): summarize, then embed the
   summary. Live events before backfill ones (#14). Each event commits as soon as each step finishes.
2. **Assign** every pool event to existing clusters ("decide then apply", below). Checked every pass,
   not only when newly embedded, so an event that was too far can join once a center moves closer.
   This includes pool events older than `CLUSTER_POOL_WINDOW_DAYS`: they can't form new clusters (Q9)
   but can still join one.
3. **Group the pool** per Signal into new clusters. Average linkage uses SciPy's standard
   implementation (replacing the prototype's hand-written one, since real pools can reach thousands
   of events). A new cluster gets its center (the average of its members) when it's created, so the
   next pass's assign can use it; step 5 updates centers after joins. One transaction per Signal: an
   error in one Signal is logged and the others carry on.
4. **Name** new clusters (Q10). The name comes from the closest member *that has a summary*; if none
   has one, the name stays empty and is tried again next pass. A repeated name within one Signal gets
   a number ("... (2)").
5. **Update cluster centers**: only clusters that gained members in step 2 (assign returns them),
   recomputed as the average of all their members. A crash before this step leaves a center slightly
   stale until that cluster's next join.

Then sleep `CLUSTERER_INTERVAL_SECONDS` (default 10).

**Running the loop** (`tripwire/clusterer/loop.py`, `__main__.py`):
- **Each step stands alone:** a failing step is logged and the pass carries on with the remaining
  steps; it's retried next pass. Safe because steps communicate through the tables; the only hand-off
  in memory is assign → centers (if assign fails, step 5 has nothing to do).
- **Autocommit connection**, opened fresh each pass, so each step's save commits on its own and a
  dropped connection heals next pass.
- **`CLUSTERER_MODE`:** `real` (default, steps 1-5; needs `OPENAI_API_KEY`) or `placeholder` (the
  skeleton's fixed embeddings, no OpenAI). Step 1 is the prepare half's `Preparer.prepare_events`, built
  once in `__main__.py` and passed as `run_pass(..., prepare=...)`; `prepare_stand_in()` remains for tests.
  Switching an existing database from placeholder to real: `docker compose down -v` (placeholder vectors
  are all identical and would be clustered together).
- **Settings** are read and checked once at startup (`settings.py`, the only place defaults live;
  `.env.example` matches, checked by a test). A bad value stops the clusterer with a message, which on
  ECS shows as an unhealthy service with the message in the logs.

**Health** (`tripwire/clusterer/health.py`, Q12): two separate checks, because they need different
responses.
- **"Is the clusterer alive?"** `python -m tripwire.clusterer.health --alive`. Every pass (even a failed
  one) records its time in `CLUSTERER_LAST_PASS_FILE`; `--alive` fails when no pass has finished for
  `CLUSTER_HEALTH_ALIVE_MINUTES`. Only a frozen loop fails it, and a restart fixes that, so **ECS runs
  this one** and restarts the task on failure.
- **"Is clustering working?"** `python -m tripwire.clusterer.health` (Q12's two rules). A restart fixes
  none of their usual causes (OpenAI down, expired key, prepare bug, database down, backlog), so the loop
  **logs them as warnings** starting with `clustering unhealthy:` (once when they start, every 5 minutes
  while they last, once when they clear) for a CloudWatch alarm on AWS. **Compose runs this one**, since
  it only displays health.
- **Infrastructure needed** (ECS `healthCheck`, CloudWatch alarm): `infra/docs/BACKEND_INFRA_NOTES.md`.

## Decisions

| # | Decision | Why (short) |
|---|---|---|
| Q1 | **Our own summarizer** writes the text that gets clustered. The judge's `description` is not embedded. | We control clustering's input; same style for every Signal; strips names and IDs. |
| Q2 | Summarizer reads: the Signal's instruction, the event (`payload`, `severity`, and `description` **if present**) and `judge_input` (the trace + earlier turns, read as opaque text). **Same rule for every Signal**, no special cases. The prototype's "hide the turn's own reply and tools" rule is **dropped**; instead the prompt says: summarize **this event's** finding and its cause, ignore what happened after it. One run can produce up to 5 events that share one `judge_input`, so the event's own `payload` is what tells them apart. Building the input lives in one function. | Can name causes the judge left out; already stored; Signals are user-defined. Several findings per run (Signals S6); `description` may be dropped (Signals open item). |
| Q3 | **No size limit** on the summarizer's input in the MVP. Log input size on every call. An input the model refuses as too long → that event's summary is `failed` at once. Summarizer model's context window ≥ the judge's. | Inputs already fit the judge; no real sizes to tune against yet. |
| Q4 | Structured output, two fields **in this order**: `cause` (what in the trace the finding comes from: tool, step, bot reply, earlier turn, user request; generic wording, fits non-failure Signals too), then `summary` (one sentence, the finding in the Signal's terms). **Only `summary` is embedded.** Small model via the **same LLM interface as the judge** (#3); its test fake (#35) applies. | Filling `cause` first makes the model commit to a cause (fights vague, magnet-forming summaries). |
| Q5 | New table **`event_summaries`** (migration `20261110220000_event_summaries.sql`), one row per event, written after the first call (no row = never tried). Status `pending` → `done` \| `failed`. Columns: `cause` (nullable, cheap to drop), `summary`, model, prompt version (integer), `attempts`, `not_before` (same name as `signal_runs`), `error_kind` (`service` / `bad_output` / `too_long` / `refused`) + `last_error` (kept after a later success), tokens and cost (totals over every paid call; cost NULL = not priced), timestamps. The database enforces: `done` has a summary; `failed` says why and is never caused by a service error. `event_embeddings` keeps meaning "this event has a vector". | Failures and paid-for summaries are never lost; home for clustering cost (#9) and prompt versions. |
| Q6 | **Clusters belong to a Signal**, not a Signal version. Each event keeps its version (#5). | Matches Laminar; edits don't wipe clusters or re-fire "new cluster" alerts. |
| Q7 | **Join rule (Friday):** nearest cluster center (same Signal) within `CLUSTER_ASSIGN_THRESHOLD` (cosine distance) → join; else the event waits in the pool. One `assign()` function with distance bands (close joins, far waits, middle band uses the threshold for now). **Later:** an LLM decides the middle band, for **joining only**: grouping (Q8) keeps the plain threshold (Nov 11). | Simple and testable; the LLM step builds on it without throwaway. |
| Q8 | **New clusters:** group pool events with the **same distance rule** as Q7 (average linkage, as in the prototype): the **threshold**, not Q7's future LLM band. Grouping compares every pool pair (far too many LLM calls) and SciPy takes one cutoff (Nov 11). A group becomes a cluster at ≥ `CLUSTER_MIN_EVENTS` events (default 3) from ≥ `CLUSTER_MIN_CONVERSATIONS` conversations (default 2). Conversation = `traces.session_id`; no session ID → each trace is its own conversation. | Fewer noise clusters; settings can reproduce the prototype's 2 / 2 rule. |
| Q9 | **Pool window:** only pool events created within `CLUSTER_POOL_WINDOW_DAYS` (default 14) can form clusters, measured from **when the event was created** (not trace time, so backfilled events can still cluster). | Avoids clusters of long-fixed problems; bounds grouping cost. |
| Q10 | **Naming (Friday):** a new cluster's name = the `summary` of its member closest to the center (skipping members without a finished summary). No LLM call. Named **once**; a repeated name within a Signal gets "(2)", "(3)". **Later:** LLM naming together with name refresh (#20). | Can't fail, never an "Unnamed cluster". |
| Q11 | **Failures:** event's own failures (bad output) → up to 3 attempts with growing waits, then `failed` with the error. Service problems (timeouts, rate limits, outages) → wait and retry **without using attempts**; the wait is per event (`not_before`, growing, capped), as the runner does (Signals S7), so one rate-limited call doesn't stall the batch. "Input too long" or a **refusal** → `failed` at once (a retry gets the same answer). Unlike the runner's 24 h give-up, a summary never gives up on a service problem; the health check (Q12) flags the stall instead. Each step commits on its own. A command re-queues failed events. No leases or claiming (single clusterer). | Failures recorded; outages heal themselves; never pays twice for a finished step. |
| Q12 | **UI:** an unclustered event shows only as gray "not in a cluster" (waiting, unclustered and failed look the same, like Laminar's "Unclustered"). Failures live in `event_summaries` + logs. **Health check unhealthy** when an unfinished **live** event is older than `CLUSTER_HEALTH_STALL_MINUTES` (default 15) **or** the last `CLUSTER_HEALTH_FAILURE_RUN` summaries (default 10) all failed. Backfill events don't count toward the first rule: they're prepared after live ones by design (#14), so they may wait (Nov 11). | Catches the one critical case: clustering silently stopping. |
| Q13 | **One loop** (above). "Decide then apply": all joining decisions in a pass use the centers as they stood at the start of the pass, then are applied together (prototype's shuffle test proves order-independence). Split into two workers later only if backfill testing shows the loop can't keep up. | Fewest moving parts; steps already communicate through tables. |
| Q14 | **Tests:** fake summarizer (scripted output; simulates timeout, broken output, too long, refusal, outage) and fake embedder; two events from one run (same `judge_input`, different `payload`) must get different prompts, each with its own payload; grouping tests **place vectors at exact distances** for boundary tests. **Calibration:** park_demo through the real judge, summarizer and embedder, scored by `report.py` (written new for these tables, see "Calibration"); sweep the threshold (e.g. 0.20–0.40). If real events aren't ready by Friday, ship `0.30`, labeled temporary. | Exact, free, repeatable tests; calibration on the only traffic with answer keys. |

**Definitions used by both halves**
- **Pool:** events with an embedding and no `cluster_members` row (unchanged from CONTRACT.md).
- **Finished clustering** (for the health check): has a cluster, or is in the pool, or its summary is `failed`.
  **Unfinished:** none of these.
- **Stored distances are "at join time":** `cluster_members.distance` is the distance to the center when
  the event joined; centers move afterwards. For "most typical events" (UI, API, naming), recompute the
  distance to the current center; don't sort by the stored column.
- **Shared SQL rules:** "in the pool", "leaf clusters only", "event → its Signal" and "unfinished" are
  written once in `tripwire/clusterer/sql.py`; every clusterer query builds from them (a test checks none
  is retyped). `check.sql` keeps a commented copy of "in the pool". Distance math is guarded by
  `tests/test_distance_guards.py` (pgvector and SciPy must agree; no approximate vector index).
- **Cluster membership is not permanent:** a later rebuild (#19) may move events. Nothing in code or UI
  may assume an event's cluster never changes.

## Settings (env vars; names proposed, defaults agreed)

| Variable | Default | Decision |
|---|---|---|
| `CLUSTERER_MODE` | `real` (`placeholder` = no OpenAI) | Running the loop |
| `OPENAI_API_KEY` | none; required in real mode | Summarizer + embedder |
| `CLUSTERER_INTERVAL_SECONDS` | 10 | Q13 |
| `CLUSTER_PREPARE_BATCH` | 50 | Q13 |
| `CLUSTER_ASSIGN_THRESHOLD` | 0.30 (temporary until calibrated) | Q7, Q14 |
| `CLUSTER_MIN_EVENTS` | 3 | Q8 |
| `CLUSTER_MIN_CONVERSATIONS` | 2 | Q8 |
| `CLUSTER_POOL_WINDOW_DAYS` | 14 | Q9 |
| `CLUSTER_HEALTH_STALL_MINUTES` | 15 | Q12 |
| `CLUSTER_HEALTH_FAILURE_RUN` | 10 | Q12 |
| `SUMMARIZER_MODEL` | `gpt-6-luna` | Q3, Q4; summarizer below |
| `SUMMARIZER_PROMPT_VERSION` | 1 (needs `prompts/summarize_v1.md` + `.schema.json`) | summarizer below |
| `CLUSTER_HEALTH_ALIVE_MINUTES` | 3 | Q12 (`--alive`) |
| `CLUSTERER_LAST_PASS_FILE` | `/tmp/clusterer-last-pass` | Q12 (`--alive`) |
| `EMBEDDING_MODEL` | `text-embedding-3-small` (1536 dims, one model only) | existing |

## Summarizer (PR 2, decided Nov 10-11)

Code: `tripwire/clusterer/summarize.py`, `llm_client.py`, `prompts/`. Full reasoning: Design Notes Q17-Q20.

- **Input** (`load_event` + `build_messages`): the instruction of the Signal version that judged *this*
  event (not the latest); Signal name and field descriptions; `severity`, `payload`, and `description` /
  evidence labels (`signal_events.evidence_labels`, Signals S25) only if present; `judge_input` unchanged. The judge's `analysis` is left out (it covers
  every finding of a run). Order: system prompt, then `<tripwire_trace>`, `<tripwire_signal>`, and this
  event's `<tripwire_finding>` **last** (focus; siblings share the trace prefix). Tags have unusual names;
  `judge_input` is not escaped. Prompt injection via traces: post-MVP, together with the Signals pair.
- **Prompt v1:** states the purpose (summaries are grouped, so the same problem must read the same), a fixed
  sentence shape (what happened, then product area), two examples from a domain we don't test, "one
  sentence, under 30 words". Strict output `{cause, summary}`. A summary over 300 characters is **logged,
  not rejected** (MVP).
- **Prompt files:** `prompts/summarize_vN.md` + `.schema.json`; never edit a used version, add the next
  number. Each row stores `prompt_version`.
- **After each call** (`decide`, pure; `save`, one upsert): good → `done`; blank field or bad output →
  retry after 1 min, `failed` on the 3rd attempt; too long / refused → `failed` at once; service error →
  `pending`, no attempt used, retry after 30 s (or OpenAI's `retry-after` if longer). Tokens and cost add up
  over every paid call. **Our own bugs are not caught here** (fail loudly); `prepare_events` handles them
  per event (below).
- **LLM client:** a stand-in shaped like the Signals pair's proposed `tripwire/llm/`; SDK retries off (one
  call = one paid request); OpenAI errors become `LLMServiceError` / `LLMBadOutput` / `LLMInputTooLong` /
  `LLMRefused`; anything else (wrong key, bad request) raises as-is. Prompt caching left at OpenAI's default.

## Prepare step (PR 3 + 4, decided Nov 11)

Code: `tripwire/clusterer/prepare.py`, `embed.py`. Full reasoning: Design Notes Q21.

- **Order:** live before backfill, then oldest first. Needs a summary = no row, or `pending` with its wait
  over. Needs a vector = `done` without an embedding. Summarize first, then embed, so an event can be
  finished in one pass.
- **Stops starting summaries** after 60 s (passes stay under `--alive`'s 3 minutes during a backlog) or
  after a service error (the OpenAI key and its rate limits are shared with the judge).
- **Our own bugs:** caught per event, logged with the traceback, and saved as `failed` with error_kind
  `crashed` and the error message (migration 004; suggested by the grouping side, Nov 11). The event then
  counts as finished: no endless "stalled" warning, no paid retries after a restart, and the re-queue SQL
  brings it back once fixed. A bug hitting every event trips the health check's "last N summaries all
  failed" rule. A **database outage** (connection errors only) is not a crash: it ends the step and the loop
  retries next pass. If even saving the crash fails, that event is skipped in memory until restart.
- **Embedder:** `text-embedding-3-small`, `summary` only, one call per batch, no failure bookkeeping (a
  failed call is retried next pass), raises on a vector that isn't 1536 numbers, no cost tracking (MVP).

## Calibration (grouping PR 6, decided Nov 12)

Code: `tripwire/clusterer/report.py`, `sweep.py`. A developer tool, never run by the deployed product.
`python -m tripwire.clusterer.report --database-url URL --answer-key <park-traffic run>/answer_key.jsonl [--sweep]`
(`--database-url` is required and never read from `DATABASE_URL`).

- **Labels:** park-traffic's answer key lists, per turn, its Laminar trace ID and the planted bugs that
  fired in it (`bugs_by_turn`), so each event gets its own turn's bugs (event → trace → turn). A bug is
  recorded on the turn where it fires; events whose turn had no bug are listed, not scored.
- **Report:** funnel (traces → runs → events → summaries → embedded → clustered / pool), bug × cluster
  table, pool events with their nearest neighbour; scores per Signal and summarizer prompt version.
- **Scores:** extended BCubed precision / recall (Amigó et al. 2009: per event, so one big cluster can't
  outweigh the rest; handles an event with two bugs). The **suggested threshold has the best F0.5**
  (precision counts twice): with F1, a cluster mixing several problems scored better than an honest split
  of one. Pairwise precision / recall shown for comparison with the prototype. A pool event counts as alone.
- **Sweep:** per threshold (default 0.20–0.40 by 0.02), the **real `run_pass()` is replayed** on a copy of
  the data in a throwaway `calib_` schema: embeddings are released in the order and passes they were
  created in (recovered from their timestamps), then passes run until nothing changes. No OpenAI calls;
  real tables untouched. Not a from-scratch regroup: that would skip joining by center.
- **Limits:** the threshold is measured on park traffic (the only traffic with an answer key for causes)
  and shipped as the default for every app. It should carry over because every app's events go through the
  same summarizer and embedder (Q1), but other domains may differ; check it on τ-bench by eye (mixed
  clusters, names mixing problems). The replay's pool window (Q9) counts from now, so replay fresh runs.
- **Upkeep:** a new table the clusterer reads goes in `sweep.COPIED_TABLES`; once an LLM decides assign's
  middle band (post-MVP), the replay needs a switch to skip or fake it, or every threshold pays for it.
- **Later:** separate thresholds for joining and for forming clusters, and merging a would-be cluster into
  a near-duplicate (Laminar has both); calibrating on a second labelled app.

## The seam between the two halves

The work is split at `event_embeddings`. See `HANDOFF_PREPARE.md` and `HANDOFF_GROUPING.md`.

| | Prepare half | Grouping half |
|---|---|---|
| Owns | `event_summaries` (new), writes `event_embeddings` | `clusters`, `cluster_members`, the loop, the health check |
| Code | `tripwire/clusterer/summarize.py`, `llm_client.py`, `embed.py`, `prepare.py` | `tripwire/clusterer/assign.py`, `pool.py`, `naming.py`, `health.py`, `__main__.py`; calibration: `report.py`, `sweep.py` |
| Interface | `Preparer.prepare_events(conn, limit) -> counts` (non-zero counts only): summarizes then embeds up to `limit` events, live first, committing per event; stops starting summaries after 60 s or a service error | The loop calls `prepare_events`, then steps 2–5 |
| Reads from the other half | nothing | `event_embeddings.embedding`; `event_summaries.summary` (naming), `.status` (health) |

Both halves read `signal_events`, `signal_versions` (→ `signal_id`) and `traces` (`session_id`), which the
Signals pair owns.

## Deferred until after the MVP

- Rebuilds (#19): frequency, keeping cluster IDs stable (a new cluster keeps an old ID when most events
  match), name refresh (#20). Use the **same distance rule** as assignment so rebuilds stay consistent.
- LLM check for the middle band (Q7), LLM naming (Q10), input size limit (Q3), two workers (Q13),
  parallel summarizer calls, failure states in the UI (Q12).
- Summarizer: growing waits (service: derive from `not_before − updated_at`, double, cap 5 min; bad output:
  1 min then 5 min), waits as settings, enforcing a summary length, comparing prompt versions (v1 vs bare
  rules vs no `cause`) on real judge output with park-traffic's answer key, prompt caching on/off.
- Re-queue command (PR 5): until then, `UPDATE event_summaries SET status = 'pending', finished_at = NULL, attempts = 0
  WHERE status = 'failed'` (add `AND error_kind = 'crashed'` to retry only crashes after a bug fix).

## Open items (outside the clusterer, or waiting on others)

- Signals pair: `judge_input` is proposed as the rendered trace + earlier turns only (not the judge's
  instructions); its text format will change, so we read it as opaque text.
- Signals pair: `description` may be dropped or made nullable; the summarizer treats it as optional (Q2).
- Signals pair: their shared LLM module (`tripwire/llm/`) has three errors; we need a fourth for refusals.
- *Answered Nov 10:* one run produces 0 to 5 events (Signals S6); earlier turns are up to 5, user message +
  reply only (S12). Our tables key on `event_id`, so neither changes our schema.
- Team: who builds the cluster API endpoints and the cluster view for Friday?
- Backfill counting rule (Q6): count traces, not events, or only the newest version's event per trace?
- Signal version markers on the Events chart.
- **Stale names and near-duplicate clusters:** a name is fixed when a cluster forms, but its center keeps
  moving, so a later cluster can form where the first one used to be and get the same name (shown as
  "(2)"). Nothing in the MVP renames or merges clusters. Should a rebuild (#19) merge near-duplicates?
  Laminar refreshes names as membership changes; Raindrop lets a person mark an issue as a duplicate.

## Laminar reference (what's documented)

From [Clusters](https://laminar.sh/docs/signals/clusters) unless noted:
- Clusters events "whose summaries are semantically similar"; each event is queued "together with its
  summary". Who writes the summary is not documented.
- "Clustering is scoped to a single Signal." Kept across Signal edits (Laminar's in-app agent, Nov 10, and
  confirmed by editing a Signal during the trial).
- Runs asynchronously in batches, "typically within a minute"; "within a batch, each event either joins
  the nearest existing group or starts a new one". Distance rule and threshold: not documented.
- Up to three levels; names "generated by Laminar's AI from representative sample events", refreshed as
  membership changes. Events not in a named cluster sit in an "Unclustered" bucket. "Expect the first
  clusters at around 50 events."
- `signal_events.signal_version` tells events from older definitions apart
  ([Changelog](https://laminar.sh/docs/changelog.md)).
