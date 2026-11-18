-- Migration 008: how each cluster got its name (Nov 17 2026, SIG-65). Owner: Clusters pair.
--
-- llm_naming.py sets all three together whenever it names or renames a cluster:
--   name_source         'llm' = the model's name; 'fallback' = the closest member's summary, used
--                       when the LLM call failed (retried at the cluster's next doubling)
--   named_at            when it was named; a rename only writes if this hasn't changed since read
--   named_member_count  the cluster's size when named; rename on growth compares against it
--
-- All three NULL = never named by llm_naming: either unnamed, or named by the old step 4
-- (naming.py, which runs when NAMING_MODEL=off). Such a name is due for an LLM rename as soon as
-- naming is on. No backfill: that rule covers every existing cluster.

ALTER TABLE clusters
    ADD COLUMN name_source text CHECK (name_source IN ('llm', 'fallback')),
    ADD COLUMN named_at timestamptz,
    ADD COLUMN named_member_count integer CHECK (named_member_count >= 1),
    ADD CONSTRAINT clusters_naming_set_together CHECK (
        (name_source IS NULL AND named_at IS NULL AND named_member_count IS NULL)
        OR (name_source IS NOT NULL AND named_at IS NOT NULL AND named_member_count IS NOT NULL
            AND name IS NOT NULL)
    );
