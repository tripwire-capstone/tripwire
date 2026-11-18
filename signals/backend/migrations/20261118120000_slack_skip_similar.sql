-- "Skip similar events" (mock-ui/SPEC.md, notifications model): only the first critical event in a
-- cluster sends a Slack alert. A critical run whose event joins a cluster that already had one is
-- recorded as skipped, with that cluster, so status can say how many repeats stayed quiet.
ALTER TABLE slack_notification_attempts
    DROP CONSTRAINT slack_notification_attempts_status_check,
    ADD CONSTRAINT slack_notification_attempts_status_check
        CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    -- The cluster a skipped alert was a repeat of. Clusters are never deleted (CONTRACT.md);
    -- SET NULL keeps the log row if that ever changes.
    ADD COLUMN cluster_id bigint REFERENCES clusters (id) ON DELETE SET NULL,
    ADD CONSTRAINT slack_notification_attempts_skipped_check
        CHECK (status <> 'skipped' OR cluster_id IS NOT NULL);
