# Handoff: the "grouping" half of the clusterer (assign, pool, name, loop, health)

*Written Tue Nov 10, 2026. For whichever Clusters-pair member builds this half, and their Claude session.
The other half (prepare: summarize + embed) is built in parallel; see `HANDOFF_PREPARE.md`.*

## Start here (for Claude)

Read, in order:
1. `docs/clustering/DESIGN.md`: every agreed decision (Q1–Q14), settings, the seam between halves.
   **Treat it as decided.** If something there looks wrong, raise it with the person you're working
   with; don't silently design around it.
2. `CONTRACT.md`: services, table owners, job flow, conventions.
3. `migrations/20261109120000_initial_schema.sql`, `tripwire/clusterer/` (placeholder),
   `tripwire/placeholder.py`, `tests/conftest.py`.

Then propose the first PR (below) before writing code.

## Scope

Take embedded events and keep each Signal's clusters up to date. Replaces the placeholder
`tripwire/clusterer/cluster.py`.

| PR | Piece | Decisions |
|---|---|---|
| 1 | **Test helper** that builds vectors at exact cosine distances, + **`assign()`**: nearest center of the same Signal within `CLUSTER_ASSIGN_THRESHOLD`, distance bands, "decide then apply" | Q7, Q13, Q14 |
| 2 | **Pool grouping** (`pool.py`): average linkage at the same threshold, `CLUSTER_MIN_EVENTS` / `CLUSTER_MIN_CONVERSATIONS`, no-session fallback, `CLUSTER_POOL_WINDOW_DAYS` by event creation time | Q8, Q9 |
| 3 | **Naming + centers** (`naming.py`): name = the `summary` of the member closest to the center; update `clusters.centroid` | Q10, Q13 |
| 4 | **The loop** (`__main__.py`): calls the prepare half's `prepare_events(conn, limit)`, then assign → group → name → centers; env var settings | Q13 |
| 5 | **Health check**: unhealthy on a stall or a run of failures. Propose the mechanism (a small `/health` endpoint per #33, or a container health-check command) | Q12 |
| 6 | **Calibration** (first stretch goal): rework `report.py` for these tables; sweep the threshold on park_demo once real events exist | Q14 |

PRs 1–3 need nothing from the prepare half: tests insert `event_embeddings` rows with hand-placed vectors.
PR 3 reads `event_summaries.summary` and PR 5 reads `event_summaries.status`, so they wait for the prepare
half's migration PR to merge (or stub the table in the test schema until then).

## What's decided for this half (details in DESIGN.md)

- **Clusters belong to a Signal** (Q6): only compare an event with clusters of its own Signal
  (`signal_events → signal_versions.signal_id`).
- **Join (Q7):** cosine distance to the nearest center ≤ threshold → join; else the event stays in the
  pool. Written as one `assign()` with bands so an LLM check can later decide the middle band.
- **Decide then apply (Q13):** all of a pass's joining decisions use the centers as they stood at the
  start of the pass; apply together; then update centers. A shuffle test proves order doesn't matter
  within a pass.
- **New clusters (Q8, Q9):** the same distance rule (average linkage); ≥ 3 events from ≥ 2 conversations
  (settings); conversation = `traces.session_id`, or the trace itself when there's none; only pool events
  created in the last 14 days (setting).
- **Naming (Q10):** no LLM; the most typical member's summary.
- **Health (Q12):** unhealthy when an unfinished event is older than 15 minutes, or the last 10 events all
  failed (settings). Reads the database, not logs.
- **Membership isn't permanent:** a later rebuild (#19) may move events; don't build anything that assumes
  otherwise.
- **Level 0 only** (#18): `parent_id` / `level` stay unused.

## Portable code

The Phase 2 prototype's `clustering/cluster.py` (repo `vkarklins/signals-prototype`; check the `clustering`
branch if it isn't on `main`) has decide-then-apply assignment, hand-written average linkage cross-checked
against scikit-learn, the at-least-2-conversations rule and the shuffle test. Port what fits; its 0.30
threshold was tuned on a different summarizer, so it's only the temporary default.

## Tests this half needs (Q14)

Boundaries with exact distances: 0.29 joins / 0.31 waits; 2 events vs 3; 1 conversation vs 2; no session
ID; a 15-day-old pool event stays out; an event never joins another Signal's cluster; shuffle test; both
health rules (true and false cases).

## Open items this half owns

- Ask the team who builds the cluster API endpoints and the cluster view for Friday.
- Backfill counting rule (DESIGN.md open items) before cluster counts appear in the UI.

## Not in scope

Summarizing, embedding, `event_summaries` (prepare half). Rebuilds, LLM assignment check, LLM naming,
hierarchy (after the MVP).

## Working agreements

- Branch + PR for everything; the other Clusters-pair member reviews.
- Propose each piece before writing code; walk the person you're working with through the core logic.
- Plain SQL migrations named `YYYYMMDDHHMMSS_what.sql`; never edit a merged one.
- Settings only from env vars (add new ones to `.env.example`).
- Don't edit the prepare half's files; agree interface changes first and update DESIGN.md's seam table.
- A decision that changes goes into DESIGN.md in the same PR.
