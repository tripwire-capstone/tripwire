# Tripwire backend contract (DRAFT)

*Draft v1, Tue Nov 10, 2026. **Nothing here is agreed yet.** Every item is a proposal for the team to
accept, change or reject. Open questions are listed at the end of each section with a checkbox.*

Purpose: let the Signals pair and the Clusters pair build in parallel from Tuesday without guessing
about each other. If it doesn't block someone this week, it isn't in here.

Exact table definitions: `migrations/20261109120000_initial_schema.sql` (migration 001).
What the skeleton already does, and what's still placeholder: `README.md` (same folder).
Decision numbers (#n) refer to "Tripwire Backend: Design Decisions to Settle".

---

## 1. MVP scope (target: Fri Nov 13)

**In**
- Traffic generator → Laminar → poller → signal runs → events → clusters (one level) → API → real pages
- Custom signals: instruction + output schema + structured output from the LLM
- Runs from one `docker compose up`

**Out (after Friday)**
- Backfill, versions UI, notifications, cluster hierarchy in the UI, data retention, extra LLM
  providers, login

**Open**
- [ ] Friday demo on AWS, or locally (AWS deploy of the skeleton as a stretch)?
- [ ] Which pages? Proposal: Signals list, Signal detail → Events tab, cluster view.

---

## 2. Services

| Service | Does | Copies | Reads | Writes |
|---|---|---|---|---|
| `poller` | Polls Laminar's SQL API for finished traces | **exactly 1** | Laminar; `signals`, `signal_versions` (if it does fan-out) | `traces`, `signal_runs` (if it does fan-out) |
| `runner` | Claims run jobs, calls the judge, records results | 1 or more | `traces`, `signal_versions`, Laminar (spans) | `signal_runs`, `signal_events` |
| `clusterer` | Summarizes and embeds new events, assigns/creates clusters, names them | **exactly 1** | `signal_events`, `signal_versions`, `signal_runs`, `traces` | `event_summaries`, `event_embeddings`, `clusters`, `cluster_members` |
| `api` | Serves the frontend | 1 or more | everything | `signals`, `signal_versions` (create/edit) |

Rules for every service:
- Settings only from env vars (section 6).
- Safe to stop at any moment and start again: no work lost, nothing done twice.
- Handles the stop signal (SIGTERM): finishes or abandons the current step, then exits.

**Open**
- [ ] Notifier: separate service later, or a step in the clusterer?

---

## 3. Tables and owners

Each table has one owning pair. Other services **read only**. Changing an owned table = a new
migration from the owner, announced to the team.

| Table | Owner | One row per |
|---|---|---|
| `projects` | shared | Laminar project we watch: its URLs and its own **encrypted** API key (one row for now, #29) |
| `traces` | ingest | finished Laminar trace we've seen |
| `signals` | signals | signal |
| `signal_versions` | signals | saved edit of a signal (full definition) |
| `signal_runs` | signals | (trace, signal version) to judge; **this is the job table** |
| `signal_events` | signals | something a judge found |
| `event_summaries` | clusters | event's clustering summary: status, errors, cost, prompt version |
| `event_embeddings` | clusters | embedded event |
| `clusters` | clusters | cluster (within one signal) |
| `cluster_members` | clusters | event's place in a leaf cluster |

Rules the database enforces (each one has a test):
- **No duplicate traces:** unique `(project_id, laminar_trace_id)`. The poller re-reads a 60 s
  overlap with `ON CONFLICT DO NOTHING`.
- **No duplicate runs:** unique `(trace_id, signal_version_id)`. Two runners, a retry, or a backfill
  over live traffic can't judge the same trace twice with the same version.
- **An event matches its run:** composite foreign key on `(run_id, trace_id, signal_version_id)`.
- **One event per run.**
- **Every event has a non-blank `description`** whatever the user's schema. User-defined fields go in
  `payload`. The clusterer does **not** embed `description`: it embeds its own summary
  (`event_summaries.summary`; `docs/clustering/DESIGN.md` Q1).
- **A summary's state is consistent:** `done` has a non-blank summary; `failed` says why (bad output,
  too long or refused), and a service error (timeout, rate limit, outage) never fails it.
- **A `running` job always has a lease; a `failed` job always has an error.**
- **Cluster membership needs an embedding;** embeddings are exactly 1536 numbers.
- **Deleting a trace removes** its runs, events, summaries, embeddings and memberships (for retention later).
  Clusters themselves stay.

Design notes:
- **Laminar keys are per project and encrypted.** Each Laminar project has its own API key, so keys
  live on `projects`, encrypted by the app (`encrypt()` / `decrypt()` in `backend/db/crypto`) with the
  master key `TRIPWIRE_SECRET_KEY`. Postgres only sees ciphertext; the API returns only the last-4 hint.
  The poller polls every project row with its own decrypted key and its own marker; one project's
  failure (e.g. an expired key) doesn't stop the others. A second project is just a second row.
- **No poller marker table.** Marker = `max(ended_at)` of `traces` for the project, so the marker and
  the data can't get out of step (prototype decision).
- **No "current version" column on `signals`.** Current = highest `version_number`. Avoids a circular
  foreign key.
- **"Found an event" vs "Found nothing"** = whether a `signal_events` row exists for a `done` run.
- **"The pool"** (events waiting for a cluster) = `event_embeddings` rows with no `cluster_members`
  row. A query, not a status column (prototype decision).
- **Clustering data stays out of `signal_events`,** so clustering changes never need a migration on
  the Signals pair's tables.
- **`judge_input` is stored on the event,** not the run: kept only when something was found (#24).
- **`clusters.parent_id` and `level`** exist so Laminar-style trees can come later (#18). MVP: level 0
  only.

**Open**
- [ ] Can one run produce more than one event (prototype summarizer produced 0–2 findings)?
- [ ] Clusters per signal (as drafted) or per signal *version*? A new version may judge differently.
- [ ] Do we store Laminar's `user_id` on traces (for filters)?
- [ ] `trigger` "specific span finishes": out of the MVP? (Only `trace_end` assumed.)
- [ ] "Every signal has at least one version": enforce in the database? Options (all tested):
      code only (API creates signal + v1 in one transaction); a deferred constraint trigger; or a
      `signals.current_version_id NOT NULL` pointer with a composite FK to `signal_versions (id, signal_id)`,
      both FKs deferrable. Leaning: pointer column (plain constraints, explicit current version, easy
      restore later; costs: get both IDs before insert, and a new version is two writes). If chosen,
      the ERD shows one-or-many on versions.

---

## 4. Job flow (`signal_runs`)

```
pending ──claim──► running ──► done
   ▲                  │  └────► failed   (3rd attempt failed, or lease expired after it)
   └──lease expired───┘
skipped   (sampled out; see open questions)
```

**Claiming** (any number of runners). Live jobs go before backfill; oldest first.
`FOR UPDATE SKIP LOCKED` means a second runner skips a row the first is claiming instead of waiting
for it or taking it.

```sql
UPDATE signal_runs
SET status = 'running', attempts = attempts + 1,
    lease_until = now() + interval '2 minutes', started_at = coalesce(started_at, now())
WHERE id = (
    SELECT id FROM signal_runs
    WHERE attempts < 3
      AND (status = 'pending' OR (status = 'running' AND lease_until < now()))
    ORDER BY (source = 'backfill'), created_at, id
    LIMIT 1
    FOR UPDATE SKIP LOCKED
)
RETURNING id, attempts;
```

**Finishing.** The runner passes back the `attempts` value it claimed with. If its lease expired and
another runner took the job over, `attempts` has moved on, the update matches 0 rows, and the late
runner rolls back instead of overwriting. Insert the event **in the same transaction**, so an event
never exists for a run that isn't `done`.

```sql
UPDATE signal_runs
SET status = 'done', lease_until = NULL, finished_at = now()  -- plus model, tokens, cost
WHERE id = %(id)s AND status = 'running' AND attempts = %(attempts)s;
-- 0 rows updated → ROLLBACK (someone else owns the job now)
```

**A judge call that fails** (timeout, bad JSON): set `last_error`, status back to `pending`, clear the
lease. After the 3rd attempt: `failed`.

**Sweep** (each runner, every loop): jobs whose runner died on the last attempt.

```sql
UPDATE signal_runs
SET status = 'failed', lease_until = NULL, finished_at = now(),
    last_error = coalesce(last_error, 'lease expired after final attempt')
WHERE status = 'running' AND lease_until < now() AND attempts >= 3;
```

**Creating jobs (fan-out).** When a trace is stored, create one `pending` row per active signal whose
current version's trigger and filters match.
- Option A: **poller**, in the same transaction as the trace insert. A crash can't leave a trace with
  no jobs. Cost: the poller reads the signals tables.
- Option B: **runner**, in a separate fan-out loop. Poller stays simple. Cost: needs a way to find
  traces not yet fanned out (e.g. a `fanned_out_at` column on `traces`).

**Sampling.** Decided by a hash, not `random()`, so reruns and retries decide the same way:

```python
h = int.from_bytes(hashlib.sha256(f"{laminar_trace_id}:{signal_id}".encode()).digest()[:8], "big")
sampled_in = h / 2**64 < sampling_rate
```

**Open**
- [ ] Fan-out: option A or B?
- [ ] Sampled-out traces: a `skipped` row (visible in the Runs tab) or no row at all?
- [ ] Hash on `trace:signal` (each signal samples different traces) or `trace` only (all signals
      sample the same traces)?
- [ ] Lease length 2 minutes? Must be longer than the slowest judge call.
- [ ] Backoff between retries, or retry right away?

---

## 5. The skeleton (branch `skeleton`)

Real traffic goes through the real poller; the runner and clusterer are placeholders that
write **real rows in the final shape**, so swapping in real logic changes nothing downstream.

- **Poller (real):** the prototype's sync, adapted. One row per finished Laminar trace.
- **Seed:** one project, one signal ("User frustration") with v1, one cluster ("Placeholder
  cluster").
- **Runner (placeholder):** for each trace with no run for an active signal's current version,
  a `done` run; for about `STUB_EVENT_RATE` of traces (hash of the trace ID), also an event with
  a canned description. No fan-out, claiming or leases yet: it finds unjudged traces with a query
  and assumes one copy.
- **Clusterer (placeholder):** for each event with no embedding, a fixed embedding; about
  `STUB_CLUSTER_RATE` of events (hash of the event ID) are linked to the signal's first cluster,
  the rest stay in the pool.

Skeleton is done when one small real traffic batch produces rows end to end (`check.sql`), and
running the poller twice over the same window adds nothing.

Temporary answers the skeleton uses for open questions (each lives in one function):
- One event per run.
- No `skipped` rows for sampled-out traces (the placeholder doesn't sample).
- Fan-out not built yet; the real runner decides between poller and runner fan-out (section 4).
- Laminar key read from `.env`; the `projects` key column holds a placeholder (section 6).

---

## 6. Conventions

- **Location:** `signals/backend/` (beside `signals/mock-ui/`). Python package `tripwire/`, one
  subpackage per service; run each with `python -m tripwire.<service>`.
- **Migrations:** `migrations/`, plain SQL, named `YYYYMMDDHHMMSS_what_it_does.sql`. For now the
  `db` container runs them on its **first** start only (empty volume); after a schema change,
  `docker compose down -v` and start again (deletes local data). Never edit a merged migration;
  add a new one.
- **Env vars:** `DATABASE_URL` (set by compose), `LMNR_PROJECT_API_KEY`, `LMNR_API_URL`,
  `LMNR_UI_URL`, `LMNR_PROJECT_ID`, `STUB_EVENT_RATE`, `STUB_CLUSTER_RATE`, optional timing
  settings, `TEST_DATABASE_URL`. Later: `TRIPWIRE_SECRET_KEY` (master key; the one secret that
  can't live in the database), `OPENAI_API_KEY`. See `.env.example`.
- **Laminar keys (target):** encrypted per project in `projects.laminar_api_key_encrypted` with
  `TRIPWIRE_SECRET_KEY`, as Laminar itself does for provider keys (`AEAD_SECRET_KEY`). The seed
  encrypts the `.env` key once; the poller decrypts per project. Skeleton: not built yet.
- **Compose service names** = section 2 names (and later the ECS service names).
- **Images:** one Dockerfile for every service; builds for amd64 and arm64 (Fargate is ARM64).

**Open**
- [ ] Migration runner (a `schema_migrations` table + small script) so new migrations apply without
      wiping the database.
- [ ] "Connect Laminar" settings page in the MVP, or the seed only?
