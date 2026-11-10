# Clustering: what the Signals pair needs to know

*Written Tue Nov 10, 2026 by the Clusters pair. For the Signals pair and any coding assistant they use.
Give this file to your assistant along with `CONTRACT.md`. The full clustering design is in `DESIGN.md`
(same folder); you only need it for details.*

## In one paragraph

The clusterer reads your `signal_events` and groups them within each Signal. It does **not** embed your
event's `description`: it writes its own summary of each event with a separate LLM call that reads the
event plus `judge_input`, then embeds that summary. So nothing about clustering needs to change in your
judge prompt or your tables. What we need from you is a stable `judge_input`, a few columns we read staying
put, and (ideally) your LLM interface so the summarizer can share it.

## What changes for you

- **`description` is no longer what gets clustered.** CONTRACT.md says it's "the text the clusterer
  embeds"; that's now wrong. It's still required and non-blank, and users read it, so write it for
  developers: specific details (park names, amounts) are fine and useful. The Clusters pair will update
  that sentence in CONTRACT.md in its migration PR.
- **You don't write a summary for clustering.** No extra output field, no extra instructions in the judge.
- **No clustering columns on your tables.** Clustering keeps its own tables (`event_summaries`,
  `event_embeddings`, `clusters`, `cluster_members`), as CONTRACT.md already says.

## What the clusterer reads from your tables

Please don't rename, drop or change the meaning of these without telling us (CONTRACT.md: changes are a
new migration from the owner, announced to the team).

| Table | Columns | Used for |
|---|---|---|
| `signal_events` | `id`, `run_id`, `trace_id`, `signal_version_id`, `created_at`, `severity`, `description`, `payload`, `judge_input` | Summarizer input; pool window (`created_at`) |
| `signal_versions` | `signal_id`, `instruction` | Clusters belong to a Signal; the summarizer reads the instruction |
| `signal_runs` | `source` (`live` / `backfill`) | Live events are clustered before backfill ones |
| `traces` | `session_id` | A new cluster needs events from at least 2 conversations |

## What we need from you (please answer)

1. **`judge_input`'s contents.** We'd like it to hold **what the judge read about the trace**: the rendered
   trace plus the earlier turns (#1), **not** the judge's own instructions (we rebuild those from the
   version if needed). Please keep its shape stable, or tell us when it changes.
   - Are earlier turns included as **full traces** or only **user message + reply**? It decides how big
     `judge_input` gets, and the summarizer reads all of it (no size limit in the MVP).
2. **The LLM interface (#3).** The summarizer uses a small model through the **same interface as the
   judge**, including its fake for tests (#35). What it needs from that interface:
   - structured output (a JSON schema in, parsed fields out);
   - token counts back (input, cached, output), for clustering cost (#9);
   - errors we can tell apart, because we retry them differently:
     - **service problems** (timeout, rate limit, provider outage, auth failure) → we wait and retry;
     - **bad output** (doesn't parse or match the schema) → we retry up to 3 times;
     - **input too long** (context length exceeded) → we stop at once.

   If your interface isn't ready, tell us its planned shape and we'll write against the same shape.
3. **Can one run produce more than one event?** Either works for us (our tables key on the event), but we'd
   like to know.
4. **Prices (#7/#8).** Whatever you use to price judge calls, we'd like to use for summarizer calls too.

## Things that touch you indirectly

- **Clusters belong to a Signal, not a version** (Q6, matching Laminar). Edits keep existing clusters;
  every event keeps its `signal_version_id`, which you already store (#5).
- **Backfill:** backfilled events are clustered like live ones, after live events. Backfill with a new
  version can give one trace two events in the same Signal; how cluster counts handle that is an open team
  question.
- **Timing:** an event joins a cluster about a minute or two after it's created (summary + embedding + the
  clusterer's 10 s loop), similar to Laminar's "a minute or two".
- **"Test on a sample trace"** results aren't saved as events, so they're never clustered (assumption;
  tell us if that's wrong).
- **Deleting a trace** removes its events, and with them their summaries, embeddings and memberships
  (cascade, per CONTRACT.md). Clusters themselves stay.
- **Notifications (#22, later):** "skip similar events" means "same cluster", so a critical event can only
  be checked once it's clustered.

## Decisions that are settled (summary)

The Clusters pair's decisions, in full with reasons in `DESIGN.md`:

| # | Decision |
|---|---|
| Q1–Q4 | Our own summarizer: reads Signal instruction + event + `judge_input`; outputs `cause` then a one-sentence `summary`; only `summary` is embedded; same rule for every Signal; no input size limit yet |
| Q5 | New `event_summaries` table (ours) |
| Q6 | Clusters per Signal, kept across versions |
| Q7–Q9 | Join the nearest cluster within a distance threshold; new clusters from ≥ 3 events in ≥ 2 conversations; 14-day pool window |
| Q10 | Cluster name = the most typical member's summary (LLM naming later) |
| Q11–Q12 | Limited retries; UI shows only "not in a cluster"; health check alarms if clustering stalls |
| Q13–Q14 | One clusterer loop; tests with fakes; threshold calibrated on park_demo |

Questions or changes: open a PR or ask the Clusters pair. If a decision changes, `DESIGN.md` changes in the
same PR.
