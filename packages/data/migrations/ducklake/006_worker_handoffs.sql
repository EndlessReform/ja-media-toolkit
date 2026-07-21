-- E2 worker handoff compaction. Individual request/result objects are staging,
-- not the durable ledger; verified item outcomes compact into this relation.

CREATE TABLE worker_handoff_items (
    request_id VARCHAR NOT NULL,
    campaign_run_id VARCHAR NOT NULL,
    step_key VARCHAR NOT NULL,
    operation VARCHAR NOT NULL,
    subject_key VARCHAR NOT NULL,
    contract_version INTEGER NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    output_fingerprint VARCHAR,
    disposition VARCHAR NOT NULL,
    result_marker_bucket VARCHAR,
    result_marker_key VARCHAR,
    requested_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    compacted_at TIMESTAMPTZ NOT NULL
);
