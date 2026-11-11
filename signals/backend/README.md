# Tripwire backend (skeleton)

A walking skeleton: every worker exists and data flows end to end. The poller and clusterer are
real; the signal runner is a **placeholder** that writes real rows in the final shape, so it can
be replaced without touching anything downstream.

```
Laminar ──► poller ──► traces ──► runner ──► signal_runs + signal_events ──► clusterer ──► event_embeddings + cluster_members
          (real)                (placeholder)                              (real)
```

Day-to-day commands (run, traffic, database): [`docs/CHEATSHEET.md`](docs/CHEATSHEET.md).
Design, table owners and open questions: [`CONTRACT.md`](CONTRACT.md).
Tables: [`migrations/20261109120000_initial_schema.sql`](migrations/20261109120000_initial_schema.sql).

## Run it

Needs Docker and a Laminar project (Cloud or self-hosted) with some traces.

```bash
cd signals/backend
cp .env.example .env            # then fill in LMNR_PROJECT_API_KEY and OPENAI_API_KEY
docker compose up --build
```

Compose starts, in order:

| Service | What it does |
|---|---|
| `db` | Postgres 16 + pgvector on `localhost:5434`. Creates the tables on its **first** start. |
| `seed` | One project, one signal ("User frustration") with v1, one cluster. Runs once, then exits. |
| `poller` | Every 10 s: new finished traces from Laminar → `traces`. Looks back 24 h on its first poll. |
| `runner` | **Placeholder.** Every 5 s: a `done` run per new trace; an event for about half of them. |
| `clusterer` | Every 10 s: summarizes and embeds new events (OpenAI; needs `OPENAI_API_KEY` in `.env`), then groups them into clusters. `CLUSTERER_MODE=placeholder` runs a no-OpenAI stand-in. |

Send some traffic (park-traffic or tau-traffic) to the same Laminar project, then:

```bash
docker compose exec -T db psql -U tripwire -d tripwire < check.sql
```

It shows rows per table, the newest trace and last poll, how many traces wait for a run (should
be 0 within seconds), and per signal: runs, events, clustered, in the pool.

**Changed a migration?** The `db` container only runs migrations on an empty database:
`docker compose down -v && docker compose up --build` (deletes local data).

## Run the tests

They use the compose database (each test in a throwaway schema), so start `db` first.

```bash
docker compose up -d db
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Layout

```
tripwire/
  poller/       laminar.py (SQL API client), poll.py (one poll of one project), __main__.py (the loop)
  runner/       run.py: PLACEHOLDER judge step
  clusterer/    cluster.py: PLACEHOLDER clustering step
  placeholder.py  hash rule + worker loop shared by the placeholders
  seed.py
tests/          poller (fake Laminar + real Postgres), seed and placeholders
migrations/     plain SQL, applied in name order
check.sql       "is data flowing?" report
```

## Next steps (to split up)

- **Real job flow (runner):** fan-out, claiming with `FOR UPDATE SKIP LOCKED` + leases, retries,
  sweep. The SQL is in CONTRACT.md section 4.
- **Real judge (runner):** fetch a trace's spans, render them as text, call the LLM with
  structured output, store the event and tokens/cost. Replaces the placeholder in `runner/run.py`.
- **Real clustering:** embeddings, assign/discover, naming. Replaces `clusterer/cluster.py`.
- **Encrypted Laminar keys:** `encrypt()`/`decrypt()` with `TRIPWIRE_SECRET_KEY`; the seed stores
  the encrypted key, the poller decrypts per project instead of reading `.env`.
- **API + mock UI:** endpoints shaped like the mock's data helpers, then wire `signals/mock-ui`
  page by page (Events tab first).
- **Migration runner:** apply new migrations without wiping the database.
- **Poller improvements**, as we find them.
