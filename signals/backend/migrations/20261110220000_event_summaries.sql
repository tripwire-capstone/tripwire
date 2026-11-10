-- Migration 002: event_summaries, the clusterer's own summary of each event (Nov 10 2026).
-- Decisions: docs/clustering/DESIGN.md Q1, Q4, Q5, Q11. Owner: Clusters pair.
--
-- Pipeline: signal_events -> summarize -> event_summaries -> embed -> event_embeddings
--
-- One row per event, written after each summarizer call (no row = never tried).
--   pending -> done | failed        (re-queue command: failed -> pending)
-- Only `summary` is embedded. event_embeddings still means "this event has a vector".

CREATE TABLE event_summaries (
    event_id             bigint PRIMARY KEY REFERENCES signal_events (id) ON DELETE CASCADE,
    status               text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'done', 'failed')),
    cause                text,            -- written first by the model (Q4); nullable, cheap to drop
    summary              text,            -- one sentence; the text that gets embedded
    model                text NOT NULL,   -- of the latest call
    prompt_version       integer NOT NULL CHECK (prompt_version >= 1),
    -- Calls that count toward the limit: the event's own outcomes (done, bad output,
    -- too long, refused). Timeouts, rate limits and outages never count (Q11).
    attempts             integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    not_before           timestamptz,     -- don't retry before this (growing waits after a
                                          -- service error or bad output); NULL = try now.
                                          -- Same name as signal_runs.not_before.
    error_kind           text CHECK (error_kind IN ('service', 'bad_output', 'too_long', 'refused')),
    last_error           text,            -- latest error; kept after a later success
    -- Totals across every paid call for this event, so clustering cost is real (#9).
    input_tokens         integer,
    cached_input_tokens  integer,
    output_tokens        integer,
    cost_usd             numeric(12, 6),  -- NULL = not priced (#7 open)
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    finished_at          timestamptz,     -- set on done / failed

    CHECK (summary IS NULL OR length(btrim(summary)) > 0),
    CHECK (error_kind IS NULL OR last_error IS NOT NULL),
    -- done: has a summary, a finish time, at least one counted call, nothing scheduled.
    CHECK (status <> 'done' OR (summary IS NOT NULL AND finished_at IS NOT NULL
                                AND attempts >= 1 AND not_before IS NULL)),
    -- failed: says why, and only the event's own failures can fail it; an outage never does.
    -- too_long and refused fail at once; bad_output after the last attempt.
    -- (IS NOT NULL is needed: a CHECK whose result is NULL passes.)
    CHECK (status <> 'failed' OR (summary IS NULL AND finished_at IS NOT NULL AND attempts >= 1
                                  AND not_before IS NULL AND error_kind IS NOT NULL
                                  AND error_kind IN ('bad_output', 'too_long', 'refused'))),
    -- pending: not finished, no summary yet.
    CHECK (status <> 'pending' OR (summary IS NULL AND finished_at IS NULL))
);
-- The prepare step looks only at unfinished rows, so the index stays small.
CREATE INDEX event_summaries_pending_idx ON event_summaries (not_before)
    WHERE status = 'pending';
