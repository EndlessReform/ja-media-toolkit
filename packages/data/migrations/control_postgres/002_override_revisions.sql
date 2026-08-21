-- A monotonic control-plane head makes human decisions usable as exact stage
-- inputs and cache keys. Wall-clock timestamps are deliberately not used as a
-- concurrency or invalidation token.

CREATE TABLE control_revisions (
    name VARCHAR(80) PRIMARY KEY,
    revision BIGINT NOT NULL
);

INSERT INTO control_revisions (name, revision)
VALUES ('binding_overrides', 0)
ON CONFLICT (name) DO NOTHING;

ALTER TABLE binding_overrides
    ADD COLUMN created_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE binding_overrides
    ADD COLUMN retired_revision BIGINT;

CREATE INDEX ix_binding_overrides_revision_history
    ON binding_overrides (created_revision, retired_revision);
