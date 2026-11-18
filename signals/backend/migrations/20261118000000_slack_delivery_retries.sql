-- Slack alerts are retried instead of being dropped after one failed send.
--
-- One row per alert, as before. A row is now a delivery log entry:
--   pending  claimed or waiting for a retry; next_try_at says when it may be tried (again)
--   sent     delivered
--   failed   gave up: Slack refused it (revoked webhook, archived channel) or retries ran out
-- Rows from before this migration were sent at most once and never retried: they count as sent.
ALTER TABLE slack_notification_attempts
    ADD COLUMN project_id  bigint REFERENCES projects (id) ON DELETE CASCADE,
    ADD COLUMN status      text NOT NULL DEFAULT 'sent'
                               CHECK (status IN ('pending', 'sent', 'failed')),
    ADD COLUMN tries       integer NOT NULL DEFAULT 1 CHECK (tries >= 1),
    ADD COLUMN next_try_at timestamptz,
    ADD COLUMN last_error  text,
    ADD COLUMN sent_at     timestamptz,
    ADD CONSTRAINT slack_notification_attempts_pending_check
        CHECK ((status = 'pending') = (next_try_at IS NOT NULL)),
    ADD CONSTRAINT slack_notification_attempts_failed_check
        CHECK (status <> 'failed' OR last_error IS NOT NULL);

ALTER TABLE slack_notification_attempts ALTER COLUMN status SET DEFAULT 'pending';

-- The notifier looks for due retries on every poll; status shows failures per project.
CREATE INDEX slack_notification_attempts_due_idx ON slack_notification_attempts (next_try_at)
    WHERE status = 'pending';
CREATE INDEX slack_notification_attempts_project_idx ON slack_notification_attempts (project_id, status);
