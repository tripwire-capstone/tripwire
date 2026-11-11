-- Migration 003: several findings per run, and run columns for retries and debugging.

-- ------------------------------------------------------------------ signal_events

-- A run produces 0..5 findings, numbered 0..4 within the run.
ALTER TABLE signal_events DROP CONSTRAINT signal_events_run_id_key;
ALTER TABLE signal_events
    ADD COLUMN finding_index smallint NOT NULL DEFAULT 0
        CHECK (finding_index BETWEEN 0 AND 4),
    -- A retried write can't insert the same finding twice.
    ADD CONSTRAINT signal_events_run_finding_key UNIQUE (run_id, finding_index);

-- Findings have no built-in text any more; text lives in payload. Nullable rather than
-- dropped because the clusterer still reads it. The non-blank check stays: NULL passes it.
ALTER TABLE signal_events ALTER COLUMN description DROP NOT NULL;

-- ------------------------------------------------------------------ signal_runs

-- No 'skipped': a trace that doesn't match a signal's filters gets no run at all.
ALTER TABLE signal_runs DROP CONSTRAINT signal_runs_status_check;
ALTER TABLE signal_runs
    ADD CONSTRAINT signal_runs_status_check
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    -- Don't claim before this: backoff after a service error or bad output.
    ADD COLUMN not_before timestamptz,
    -- The judge's reasoning and raw answer, kept on every finished run.
    ADD COLUMN analysis text,
    ADD COLUMN raw_output text,
    -- What the judge read, kept only when the run failed for good.
    ADD COLUMN rendered_trace text,
    ADD CONSTRAINT signal_runs_rendered_trace_failed_check
        CHECK (rendered_trace IS NULL OR status = 'failed'),
    -- Findings removed because their evidence didn't check out.
    ADD COLUMN dropped_findings integer NOT NULL DEFAULT 0 CHECK (dropped_findings >= 0);

COMMENT ON COLUMN signal_runs.input_tokens IS 'Total across every attempt, failed ones included';
COMMENT ON COLUMN signal_runs.cost_usd IS 'Total across every attempt; NULL if any call was unpriced';

-- ------------------------------------------------------------------ signal_versions

-- Optional "what counts as critical" text for the judge.
ALTER TABLE signal_versions
    ADD COLUMN critical_guidance text
        CHECK (critical_guidance IS NULL OR length(btrim(critical_guidance)) > 0);
        