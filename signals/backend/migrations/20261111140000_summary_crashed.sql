-- Migration 004: a summary can fail because OUR code crashed on the event (Clusters pair, Nov 11).
-- Decided in Design Notes Q21 (follow-up): instead of an in-memory skip list, prepare_events records a
-- crash as status 'failed', error_kind 'crashed', with the error message. The event then counts as
-- finished (no endless "stalled" warning from the health check), survives restarts (never retried and
-- paid for again by accident), and comes back with the re-queue SQL once the bug is fixed.
-- A database outage is NOT a crash: it ends the step and the loop retries next pass.

ALTER TABLE event_summaries DROP CONSTRAINT event_summaries_error_kind_check;
ALTER TABLE event_summaries ADD CONSTRAINT event_summaries_error_kind_check
    CHECK (error_kind IN ('service', 'bad_output', 'too_long', 'refused', 'crashed'));

-- failed: says why, and only the event's own failures (or a crash on it) can fail it; never an outage.
ALTER TABLE event_summaries DROP CONSTRAINT event_summaries_check2;
ALTER TABLE event_summaries ADD CONSTRAINT event_summaries_failed_check
    CHECK (status <> 'failed' OR (summary IS NULL AND finished_at IS NOT NULL AND attempts >= 1
                                  AND not_before IS NULL AND error_kind IS NOT NULL
                                  AND error_kind IN ('bad_output', 'too_long', 'refused', 'crashed')));
