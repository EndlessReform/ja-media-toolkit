-- Phase D2 execution metadata. A pipeline run is one operator dispatch; each
-- stage is an independently atomic checkpoint backed by a DuckLake snapshot.
-- Materialization metadata is append-only so current state and lineage can be
-- recovered without treating a multi-stage run as one transaction.

ALTER TABLE materializations ADD COLUMN materialization_id VARCHAR;
ALTER TABLE materializations ADD COLUMN recipe_revision VARCHAR;
ALTER TABLE materializations ADD COLUMN build_key VARCHAR;
ALTER TABLE materializations ADD COLUMN input_heads JSON;
ALTER TABLE materializations ADD COLUMN snapshot_id BIGINT;

UPDATE materializations
SET materialization_id = coalesce(
    materialization_id,
    'legacy-' || target || '-' || coalesce(run_id, cast(epoch(computed_at) AS VARCHAR))
);

CREATE TABLE pipeline_runs (
    run_id VARCHAR NOT NULL,
    target VARCHAR NOT NULL,
    forced_from_stage VARCHAR,
    status VARCHAR NOT NULL,
    machine VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    terminal_snapshot_id BIGINT,
    override_revision BIGINT NOT NULL,
    error_message VARCHAR
);

CREATE TABLE run_stage_checkpoints (
    checkpoint_id VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    stage VARCHAR NOT NULL,
    ordinal INTEGER NOT NULL,
    disposition VARCHAR NOT NULL,
    attempt_id VARCHAR,
    materialization_id VARCHAR,
    recipe_revision VARCHAR NOT NULL,
    build_key VARCHAR NOT NULL,
    input_heads JSON NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message VARCHAR
);
