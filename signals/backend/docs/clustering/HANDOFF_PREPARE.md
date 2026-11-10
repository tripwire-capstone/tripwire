# Handoff: the "prepare" half of the clusterer (summarize + embed)

*Written Tue Nov 10, 2026. For whichever Clusters-pair member builds this half, and their Claude session.
The other half (grouping) is built in parallel; see `HANDOFF_GROUPING.md`.*

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

Turn each new `signal_events` row into an `event_summaries` row and an `event_embeddings` row.

| PR | Piece | Decisions |
|---|---|---|
| 1 | **Migration** `migrations/<timestamp>_event_summaries.sql`: the `event_summaries` table. Update CONTRACT.md's table list and owners. Announce to the team. **Merge first: the grouping half reads this table.** | Q5 |
| 2 | **Summarizer** (`summarize.py`): input, prompt v1, structured output, failure handling, fake | Q2, Q3, Q4, Q11 |
| 3 | **Embedder** (`embed.py`): real (`text-embedding-3-small`) and fake | Q14 |
| 4 | **`prepare_events(conn, limit)`** (`prepare.py`): picks up to `limit` events with no finished summary/embedding, live before backfill, summarizes, embeds, commits per event; returns counts | Q11, Q13 |
| 5 | **Re-queue command** for failed events (e.g. `python -m tripwire.clusterer.requeue`) | Q11 |

## What's decided for this half (details in DESIGN.md)

- **Input (Q2):** the Signal's instruction + the event's `description`, `payload`, `severity` +
  `judge_input`. Same for every Signal; no Signal-specific rules.
- **Output (Q4):** structured, `cause` first, then `summary` (one sentence). Only `summary` is embedded.
- **Prompt v1 must say** (Q2, Q4): summarize the judge's finding and its cause, in the Signal's own terms
  (a failure and its cause, or an intent, etc.); ignore anything that happened after the problem; leave out
  names, IDs, amounts and dates; keep the product area (e.g. "trail details", "booking").
  Expect to iterate: record a prompt version on every row.
- **No input size limit (Q3):** log the input size of every call; "too long" → `failed` at once.
- **Failures (Q11):** event's own failures up to 3 attempts with growing waits, then `failed` + error;
  timeouts, rate limits, outages wait without using attempts; never pay twice for a finished step
  (summary saved before embedding).
- **Model:** small, through the **same LLM interface as the judge** (#3), with its fake (#35).
- **Cost (#9):** tokens and cost on the summary row; clustering cost is tracked separately from
  "Signal run cost". (#7/#8, how prices are looked up, is still open team-wide.)

## Dependencies on the Signals pair (this half owns these conversations)

The questions below are written up for them in `FOR_SIGNALS_PAIR.md`; their answers go into DESIGN.md.

- **`judge_input`'s contents and shape.** Ask for: the rendered trace + earlier turns only, not the judge's
  own instructions; stable shape; are earlier turns full traces or only user message + reply? Until
  answered, build against "trace + earlier turns as text" and keep the reading in one function.
- **The judge's LLM interface (#3).** If it isn't ready, write the summarizer against a small interface of
  the same shape and swap when theirs lands. Agree the shape with them first.
- **`description` is no longer what gets embedded.** Let them know (CONTRACT.md says otherwise).

## Tests this half needs (Q14)

- Fake summarizer covering: normal output, timeout, broken output, "too long", outage (each Q11 path,
  including "outage waits don't use attempts").
- Restart safety: a summary saved but not embedded is embedded on the next run without a second LLM call.
- Live events are prepared before backfill ones.
- Fake embedder returns 1536 numbers, the same for the same text.

## Not in scope

Assigning, grouping, naming, the loop, the health check (grouping half). Rebuilds, LLM naming, size limits
(after the MVP).

## Working agreements

- Branch + PR for everything; the other Clusters-pair member reviews.
- Propose each piece before writing code; walk the person you're working with through the core logic.
- Plain SQL migrations named `YYYYMMDDHHMMSS_what.sql`; never edit a merged one.
- Settings only from env vars (add new ones to `.env.example`).
- Don't edit the grouping half's files; agree interface changes first and update DESIGN.md's seam table.
- A decision that changes goes into DESIGN.md in the same PR.
