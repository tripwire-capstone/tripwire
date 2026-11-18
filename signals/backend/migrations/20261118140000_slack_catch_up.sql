-- Catch-up note: after alerts fail for good (Slack down, webhook broken), the next alert that gets
-- through is followed by one note saying how many were missed. This marks the failed alerts that
-- note already counted, so each one is mentioned once.
ALTER TABLE slack_notification_attempts
    ADD COLUMN caught_up_at timestamptz;
