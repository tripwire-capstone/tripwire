-- The span labels (e.g. S3) a finding cites, as they appear in judge_input. Lets a reader of
-- an event find its part of the rendered trace when one run produced several events.
ALTER TABLE signal_events
    ADD COLUMN evidence_labels text[] NOT NULL DEFAULT '{}';