-- Final Phase C2 contracts. Automatic products are replaced as complete
-- batches; human binding decisions live in ordinary PostgreSQL.

CREATE TABLE bronze_captures (
    capture_id VARCHAR,
    series_namespace VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    manifest_bucket VARCHAR NOT NULL,
    manifest_key VARCHAR NOT NULL,
    manifest_etag VARCHAR NOT NULL,
    manifest_schema_version INTEGER NOT NULL,
    first_observed_at TIMESTAMPTZ NOT NULL,
    last_observed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE episode_hints_auto (
    hint_id VARCHAR,
    capture_id VARCHAR NOT NULL,
    candidate_namespace VARCHAR NOT NULL,
    candidate_series_id VARCHAR NOT NULL,
    candidate_episode VARCHAR NOT NULL,
    method VARCHAR NOT NULL,
    confidence DOUBLE,
    evidence JSON NOT NULL,
    input_data_version VARCHAR NOT NULL,
    recipe_version VARCHAR NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_source VARCHAR
);

CREATE TABLE episode_bindings_auto (
    binding_id VARCHAR,
    namespace VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    episode VARCHAR NOT NULL,
    audio_capture_id VARCHAR NOT NULL,
    decision_method VARCHAR NOT NULL,
    decision_evidence JSON NOT NULL,
    input_data_version VARCHAR NOT NULL,
    recipe_version VARCHAR NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_source VARCHAR
);

CREATE TABLE resolution_issues_auto (
    issue_id VARCHAR,
    capture_id VARCHAR NOT NULL,
    hint_id VARCHAR,
    kind VARCHAR NOT NULL,
    details JSON NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_source VARCHAR
);

CREATE TABLE materializations (
    target VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    fingerprint VARCHAR NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_id VARCHAR
);

CREATE TABLE run_log (
    run_event_id VARCHAR,
    run_id VARCHAR NOT NULL,
    stage VARCHAR NOT NULL,
    target_selector JSON NOT NULL,
    input_fingerprints JSON NOT NULL,
    recipe_version VARCHAR,
    machine VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    exit_code INTEGER,
    log_uri VARCHAR,
    error_message VARCHAR,
    run_source VARCHAR
);
