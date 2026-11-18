CREATE TABLE slack_notifier_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    enabled_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE slack_notification_attempts (
    notification_type text NOT NULL
        CHECK (notification_type IN ('critical_run', 'new_cluster')),
    record_id bigint NOT NULL,
    attempted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (notification_type, record_id)
);
