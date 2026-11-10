-- Migration 001: initial schema for Tripwire's backend (DRAFT, Nov 9 2026).
-- Every table here is a proposal; see docs/backend-contract.md for owners, rules and
-- open questions. Nothing is agreed until the team signs off.
--
-- Pipeline: poller -> traces -> signal_runs (jobs) -> signal_events
--           -> event_embeddings -> clusters / cluster_members

CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------------ shared

-- One row per Laminar project we watch. One row for now (decision #29); the table
-- exists so a second project is just a second row.
-- Each Laminar project has its own API key, so keys live here, encrypted by the app
-- with the master key from TRIPWIRE_SECRET_KEY. Postgres only ever sees ciphertext;
-- the API never returns the key, only the hint.
CREATE TABLE projects (
    id                         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                       text NOT NULL UNIQUE,
    laminar_api_url            text NOT NULL,   -- where the poller sends SQL queries
    laminar_ui_url             text NOT NULL,   -- base for "Open in Laminar" links
    laminar_project_id         uuid,            -- part of those links
    laminar_api_key_encrypted  bytea NOT NULL,
    laminar_api_key_hint       text NOT NULL,   -- last 4 characters, for display
    last_polled_at             timestamptz,     -- last successful poll; a quiet app and a
                                                -- broken poller look different
    created_at                 timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------ ingest (poller)

-- One row per finished Laminar trace we've seen. Laminar keeps the full trace; we
-- keep only what we need to schedule and filter work (decision #24).
-- The poller's marker is max(ended_at) per project: no separate marker table, so the
-- marker can't get out of step with the data.
CREATE TABLE traces (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id        bigint NOT NULL REFERENCES projects (id),
    laminar_trace_id  uuid NOT NULL,
    session_id        text,            -- NULL when the app sends no session ID
    started_at        timestamptz NOT NULL,
    ended_at          timestamptz NOT NULL,
    status            text NOT NULL CHECK (status IN ('success', 'error')),
    metadata          jsonb NOT NULL DEFAULT '{}',   -- Laminar trace metadata, for filters
    synced_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, laminar_trace_id),           -- re-reading the overlap is harmless
    CHECK (ended_at >= started_at)
);
CREATE INDEX traces_marker_idx ON traces (project_id, ended_at);
CREATE INDEX traces_session_idx ON traces (project_id, session_id);

-- ------------------------------------------------------------------ signals

CREATE TABLE signals (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id  bigint NOT NULL REFERENCES projects (id),
    name        text NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

-- Every saved edit is a new row with the full definition (agreed Nov 5).
-- The current version is the one with the highest version_number: no pointer column
-- on signals, so there's no circular foreign key and nothing to keep in sync.
CREATE TABLE signal_versions (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    signal_id       bigint NOT NULL REFERENCES signals (id),
    version_number  integer NOT NULL CHECK (version_number >= 1),
    instruction     text NOT NULL,
    output_schema   jsonb NOT NULL,                       -- user-defined typed fields
    trigger         jsonb NOT NULL DEFAULT '{"type": "trace_end"}',
    filters         jsonb NOT NULL DEFAULT '[]',
    sampling_rate   numeric(5, 4) NOT NULL DEFAULT 1
                        CHECK (sampling_rate > 0 AND sampling_rate <= 1),
    model           text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (signal_id, version_number)
);

-- The job table: one row per (trace, signal version) to judge. A runner claims a row,
-- calls the judge, and records the outcome and cost on the same row.
--   pending -> running -> done | failed      (skipped = sampled out; open question)
-- "Found an event" vs "Found nothing" = whether a signal_events row exists for the run.
CREATE TABLE signal_runs (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trace_id             bigint NOT NULL REFERENCES traces (id) ON DELETE CASCADE,
    signal_version_id    bigint NOT NULL REFERENCES signal_versions (id),
    source               text NOT NULL DEFAULT 'live' CHECK (source IN ('live', 'backfill')),
    status               text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'running', 'done', 'failed', 'skipped')),
    attempts             integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease_until          timestamptz,     -- set when claimed; expired = up for grabs again
    last_error           text,
    model                text,
    input_tokens         integer,
    cached_input_tokens  integer,
    output_tokens        integer,
    cost_usd             numeric(12, 6),  -- computed at run time; prices change (#7)
    created_at           timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,     -- first claim
    finished_at          timestamptz,
    -- The duplicate guard: two runners, a retry after a crash, or a backfill over live
    -- traffic can never produce two runs of one version on one trace (#12).
    UNIQUE (trace_id, signal_version_id),
    -- Lets signal_events prove its trace and version match its run (see below).
    UNIQUE (id, trace_id, signal_version_id),
    -- A running job always has a lease, or a crashed runner would hold it forever.
    CHECK (status <> 'running' OR lease_until IS NOT NULL),
    -- Finished jobs have a finish time.
    CHECK (status NOT IN ('done', 'failed') OR finished_at IS NOT NULL),
    -- A failed job says why.
    CHECK (status <> 'failed' OR last_error IS NOT NULL)
);
-- Claiming looks only at unfinished jobs, so the index stays small.
CREATE INDEX signal_runs_claim_idx ON signal_runs (created_at)
    WHERE status IN ('pending', 'running');
CREATE INDEX signal_runs_version_idx ON signal_runs (signal_version_id, created_at);

-- What a judge found. trace_id and signal_version_id are copied from the run so pages
-- can query events directly; the composite foreign key makes a mismatch impossible.
CREATE TABLE signal_events (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id             bigint NOT NULL UNIQUE,     -- one event per run (open question)
    trace_id           bigint NOT NULL,
    signal_version_id  bigint NOT NULL,
    severity           text NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    -- Built-in, always present, whatever the user's schema: the text clustering embeds.
    description        text NOT NULL CHECK (length(btrim(description)) > 0),
    payload            jsonb NOT NULL DEFAULT '{}',   -- the user's schema fields
    span_ids           text[] NOT NULL DEFAULT '{}',  -- evidence, for span links (#10)
    judge_input        text NOT NULL,   -- what the judge read; kept only when it found something
    created_at         timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id, trace_id, signal_version_id)
        REFERENCES signal_runs (id, trace_id, signal_version_id) ON DELETE CASCADE
);
CREATE INDEX signal_events_version_idx ON signal_events (signal_version_id, created_at);
CREATE INDEX signal_events_trace_idx ON signal_events (trace_id);

-- ------------------------------------------------------------------ clusters

-- Clustering's own tables: the signals pair never migrates for clustering changes.

-- One embedding per event. "The pool" (events waiting for a cluster) = rows here with
-- no cluster_members row.
CREATE TABLE event_embeddings (
    event_id     bigint PRIMARY KEY REFERENCES signal_events (id) ON DELETE CASCADE,
    model        text NOT NULL,          -- one embedding model enforced by the code
    embedding    vector(1536) NOT NULL,  -- text-embedding-3-small
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Clusters live inside a signal (decided Nov 3, following Laminar). parent_id and
-- level allow Laminar-style trees later (#18); the MVP uses level 0 only.
CREATE TABLE clusters (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    signal_id   bigint NOT NULL REFERENCES signals (id),
    parent_id   bigint REFERENCES clusters (id),
    level       smallint NOT NULL DEFAULT 0 CHECK (level BETWEEN 0 AND 2),  -- 0 = leaf
    name        text,                   -- NULL until named
    description text,
    centroid    vector(1536),           -- leaf clusters only
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (parent_id IS NULL OR parent_id <> id)
);
CREATE INDEX clusters_signal_idx ON clusters (signal_id);

-- Which leaf cluster an event belongs to. One cluster per event.
CREATE TABLE cluster_members (
    event_id     bigint PRIMARY KEY REFERENCES event_embeddings (event_id) ON DELETE CASCADE,
    cluster_id   bigint NOT NULL REFERENCES clusters (id),
    distance     real,                   -- to the centroid when assigned
    assigned_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cluster_members_cluster_idx ON cluster_members (cluster_id);
