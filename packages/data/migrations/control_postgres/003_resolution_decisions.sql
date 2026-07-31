-- Human-approved agent drafts are atomic, reviewable control-plane changes.
-- Capture dispositions cover extras that deliberately have no episode locator.

CREATE TABLE capture_dispositions (
    disposition_id VARCHAR(80) PRIMARY KEY,
    capture_id VARCHAR(80) NOT NULL,
    disposition VARCHAR(40) NOT NULL,
    decision_note TEXT,
    created_revision BIGINT NOT NULL,
    retired_revision BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at TIMESTAMPTZ,
    CHECK (disposition = 'leave_out_of_episode_index'),
    CHECK (retired_at IS NULL OR retired_at >= created_at)
);

CREATE UNIQUE INDEX uq_capture_dispositions_current_capture
    ON capture_dispositions (capture_id) WHERE retired_at IS NULL;

CREATE INDEX ix_capture_dispositions_revision_history
    ON capture_dispositions (created_revision, retired_revision);

INSERT INTO control_revisions (name, revision)
VALUES ('capture_dispositions', 0)
ON CONFLICT (name) DO NOTHING;

CREATE TABLE resolution_decision_batches (
    batch_id VARCHAR(80) PRIMARY KEY,
    action VARCHAR(16) NOT NULL,
    reverses_batch_id VARCHAR(80) REFERENCES resolution_decision_batches(batch_id),
    environment VARCHAR(16) NOT NULL,
    current_anilist_id BIGINT NOT NULL,
    summary TEXT NOT NULL,
    reason TEXT,
    resolution_materialization_id VARCHAR(160) NOT NULL,
    snapshot_id BIGINT NOT NULL,
    base_binding_revision BIGINT NOT NULL,
    applied_binding_revision BIGINT NOT NULL,
    base_disposition_revision BIGINT NOT NULL,
    applied_disposition_revision BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (action IN ('accept', 'reverse')),
    CHECK ((action = 'accept' AND reverses_batch_id IS NULL)
        OR (action = 'reverse' AND reverses_batch_id IS NOT NULL))
);

CREATE UNIQUE INDEX uq_resolution_batch_reversal
    ON resolution_decision_batches (reverses_batch_id)
    WHERE action = 'reverse';

CREATE TABLE resolution_decision_items (
    batch_id VARCHAR(80) NOT NULL REFERENCES resolution_decision_batches(batch_id),
    item_index INTEGER NOT NULL,
    capture_id VARCHAR(80) NOT NULL,
    decision VARCHAR(40) NOT NULL,
    destination_anilist_id BIGINT,
    destination_episode VARCHAR(80),
    rationale TEXT NOT NULL,
    applied_control_id VARCHAR(80) NOT NULL,
    prior_capture_id VARCHAR(80),
    prior_method VARCHAR(80),
    prior_note TEXT,
    prior_disposition VARCHAR(40),
    prior_disposition_note TEXT,
    PRIMARY KEY (batch_id, item_index)
);

CREATE INDEX ix_resolution_decision_items_capture
    ON resolution_decision_items (capture_id, batch_id);
