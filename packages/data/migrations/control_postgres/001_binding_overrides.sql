-- Human episode-binding decisions live in ordinary PostgreSQL, outside the
-- DuckLake catalog schema. Retired rows preserve decision history while the
-- partial indexes make PostgreSQL enforce the two current-head invariants.

CREATE TABLE binding_overrides (
    override_id VARCHAR(80) PRIMARY KEY,
    namespace VARCHAR(32) NOT NULL,
    series_id VARCHAR(80) NOT NULL,
    episode VARCHAR(80) NOT NULL,
    audio_capture_id VARCHAR(80),
    decision_method VARCHAR(80) NOT NULL,
    decision_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at TIMESTAMPTZ,
    CHECK (retired_at IS NULL OR retired_at >= created_at)
);

CREATE UNIQUE INDEX uq_binding_overrides_current_locator
    ON binding_overrides (namespace, series_id, episode)
    WHERE retired_at IS NULL;

CREATE UNIQUE INDEX uq_binding_overrides_current_capture
    ON binding_overrides (audio_capture_id)
    WHERE retired_at IS NULL AND audio_capture_id IS NOT NULL;

CREATE INDEX ix_binding_overrides_locator_history
    ON binding_overrides (namespace, series_id, episode, created_at DESC);
