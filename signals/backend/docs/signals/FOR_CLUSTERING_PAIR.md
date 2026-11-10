# Signals: what changes for the Clusters pair

*Tue Nov 10, 2026, from the Signals pair. The detailed version for your coding assistants is
`FOR_CLUSTERING_AGENTS.md`; our full decisions are in `DESIGN.md`.*

## Your questions, answered

- **More than one event per run?** Yes, 0 to 5, one per distinct problem. Needs a migration from us
  (announced first).
- **Earlier turns in `judge_input`: full traces?** No. Up to 5 earlier turns, user message + reply only.
- **`judge_input` contents?** Likely the rendered trace + earlier turns, without the judge's
  instructions, but the format isn't final. Treat it as plain text for now.
- **LLM interface and prices?** We'll share one module. Proposed shape in the agents doc; let's agree it
  together before either side builds. Prices not decided yet.

## Things that affect your design

1. **Events from the same turn share `judge_input`.** Your summarizer should summarize *the event's
   finding* (its payload and severity), not the whole trace, or two findings from one turn will cluster
   together.
2. **`description` might go away.** We're considering keeping all event text in the signal's own fields
   (`payload`). Please don't make `description` required for the summarizer.
3. **Evidence spans aren't linked to `judge_input`.** Events store real Laminar span IDs; `judge_input`
   uses short labels. Tell us if you want the labels stored too.
4. **Events can arrive late.** Retries during an LLM outage can delay an event up to 24 h after its
   trace. Your pool window uses `created_at`, which handles it.
5. **The judge's reasoning will be stored on each run**, if you want it as extra input for `cause`.

## Nothing changes for

Your tables, clusters per signal, `session_id`, `source` (live / backfill), severity values, and "test on
a sample trace" never creating events.
