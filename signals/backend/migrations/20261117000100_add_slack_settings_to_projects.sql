ALTER TABLE projects
    ADD COLUMN slack_webhook_url_encrypted bytea,
    ADD COLUMN slack_enabled_at timestamptz,
    ADD CONSTRAINT projects_slack_settings_pair_check
        CHECK (
            (slack_webhook_url_encrypted IS NULL) =
            (slack_enabled_at IS NULL)
        );
