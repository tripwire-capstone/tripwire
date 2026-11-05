-- Signals prototype: database schema (Postgres 12+).
-- One file for both tracks; see docs/CONTRACT.md (v1.1).
--   turns    Track A (sync) writes one row per turn item; Track B reads it.
--   results  Track B stores one probability per turn, signal, and question version.
--
-- Apply to a fresh database:  psql -d <database> -f schema.sql

-- ---------------------------------------------------------------- turns (Track A)

CREATE TABLE turns (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 'live' = synced from Laminar; 'gold' = loaded from eval/gold/*.jsonl.
    source          text NOT NULL CHECK (source IN ('live', 'gold')),

    -- The turn item exactly as in docs/CONTRACT.md: the target turn plus up to
    -- 10 earlier turns. This is what the classifier reads. Rows never change.
    item            jsonb NOT NULL
                        CONSTRAINT turns_item_contract_v1 CHECK (item ->> 'contract' = 'v1'),

    -- Computed from item by Postgres, so they can never disagree with it.
    -- The target is always the last element of item.turns (-1 = last).
    trace_id        uuid GENERATED ALWAYS AS
                        ((item -> 'turns' -> -1 ->> 'trace_id')::uuid) STORED,
    conversation_id text NOT NULL GENERATED ALWAYS AS
                        (item ->> 'conversation_id') STORED,
    target_index    integer NOT NULL GENERATED ALWAYS AS
                        ((item ->> 'target_turn')::integer) STORED,

    -- Gold rows only: the gold file's case_id, to join results to the labels.
    gold_case_id    text,

    -- Live rows only: the root span's end_time in Laminar.
    -- The polling marker is max(turn_ended_at) over live rows.
    turn_ended_at   timestamptz,

    created_at      timestamptz NOT NULL DEFAULT now(),

    -- Dedupe: a turn is written once. UNIQUE allows any number of NULLs,
    -- so gold rows (trace_id NULL) are fine.
    CONSTRAINT turns_trace_id_key UNIQUE (trace_id),
    CONSTRAINT turns_gold_case_id_key UNIQUE (gold_case_id),

    -- A live row without a trace_id would slip past the dedupe silently,
    -- and one without turn_ended_at would be invisible to the marker.
    CONSTRAINT turns_live_fields CHECK (
        source <> 'live'
        OR (trace_id IS NOT NULL AND turn_ended_at IS NOT NULL AND gold_case_id IS NULL)
    ),
    CONSTRAINT turns_gold_fields CHECK (
        source <> 'gold'
        OR (gold_case_id IS NOT NULL AND turn_ended_at IS NULL)
    )
);

-- For the polling marker: SELECT max(turn_ended_at) FROM turns WHERE source = 'live';
CREATE INDEX turns_live_ended_at_idx ON turns (turn_ended_at) WHERE source = 'live';

-- ---------------------------------------------------------------- results (Track B)

CREATE TABLE results (
    turn_id          bigint NOT NULL REFERENCES turns(id),
    signal           text NOT NULL,
    question_version text NOT NULL,
    probability      real NOT NULL CHECK (probability BETWEEN 0 AND 1),

    -- Keep one score for each signal/question version on each turn.
    CONSTRAINT results_turn_signal_question_key
        PRIMARY KEY (turn_id, signal, question_version)
);
