-- Migration 007: event_summaries.details (Nov 16 2026, SIG-64). Owner: Clusters pair.
--
-- Prompt v2+ also returns `details`: short "kind: value" strings for the specific things a finding
-- involves ("park: Glacier Bay", "tool: book_campsite"). They are kept out of `summary`, so they are
-- NOT embedded and don't change grouping; naming reads them. NULL = written by a prompt without
-- details (v1) or not summarized yet. An empty array = the model found nothing specific.

ALTER TABLE event_summaries ADD COLUMN details jsonb
    CHECK (details IS NULL OR jsonb_typeof(details) = 'array');
