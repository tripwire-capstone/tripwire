-- Which built-in template a signal was created from (e.g. 'failure'); NULL for a custom signal.
-- The signal is an independent copy: editing it never changes the template, and template
-- updates never change existing signals.
ALTER TABLE signals
    ADD COLUMN template text CHECK (template IS NULL OR length(btrim(template)) > 0);