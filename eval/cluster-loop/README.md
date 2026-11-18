# Cluster loop: the frozen scorer (phase 0)

`./score` scores Tripwire's clustering on saved runs. Phase 1 (improving clusters) may not change
`scorer/`; `./score hash` proves it didn't.

```
./score restore --set tuning           # pg_restore each run's dump into its own database (idempotent)
./score check                          # acceptance test: the spec's hand-computed numbers
./score run --set tuning               # replay with the working-tree code, write out/<UTC>/scores.{md,json}
./score run --runs park1b --as-version 5   # re-summarize first (paid, ~$0.30)
./score run --runs park2 --naming      # name replayed clusters with the real namer (paid, cents)
./score gate --run SEALED_ID           # sealed runs: aggregates only, logged to GATE_LOG.md, 3 uses each
./score hash                           # sha256 of scorer/
```

Runs are listed in `runs.json`. Databases live in the docker compose Postgres (`TEST_DATABASE_URL`
in `signals/backend/.env`, database name swapped); the live `tripwire` database is refused
everywhere. `docker compose down -v` deletes them; `./score restore` rebuilds them.

## What is computed (per run, per signal)

* **A. Answer key (park):** BCubed P/R/F0.5 (`tripwire.clusterer.report.bcubed`). Label = the
  answer key's bugs for the event's trace (`turn` for Issues, `sofar` for User frustration);
  unlabeled events left out; unclustered = alone. A Laminar event in two clusters uses the lowest id.
* **B. Laminar agreement:** per trace, Tripwire's most common cluster (a tie keeps every tied
  cluster, decided Nov 18; scored with the multi-cluster Extended BCubed) vs
  Laminar's (lowest named level >= 1; an event in two clusters there counts for both, decided
  Nov 18). Over shared traces Laminar clustered: BCubed with Laminar as the label. Plus a mapping
  table.
* **C. Coverage:** events, clusters, % unclustered, largest cluster's share.
* **D. Naming (`--naming`):** keyword check (park), rubric grade (placeholder model and rubric,
  only with `--grader-model`), Laminar name similarity.

Replays use `settings.py` defaults (not the shell's `CLUSTER_*` variables), the given
`--threshold`, and `CLUSTER_POOL_WINDOW_DAYS=36500` so old runs replay the same on any date.

## Restore detail

The dumps predate migrations that add columns in the middle of `projects`, and the replay copies
rows by position, so `restore` builds the schema from `signals/backend/migrations/` and loads only
the dump's data (`pg_restore --data-only`, by column name). If a later migration changes a table,
drop the run databases and restore again.

Tests: `../../signals/backend/.venv/bin/python -m pytest tests` (real Postgres, fake LLM and embedder).
