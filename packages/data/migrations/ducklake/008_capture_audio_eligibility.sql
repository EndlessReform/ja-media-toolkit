-- Keep capture-level audio admission separate from canonical episode/subtitle
-- inputs. Rejections remain durable and inspectable without polluting the
-- positive canonical contract or aborting a corpus materialization.

CREATE TABLE capture_audio_eligibility (
    capture_id VARCHAR NOT NULL,
    manifest_bucket VARCHAR NOT NULL,
    manifest_key VARCHAR NOT NULL,
    manifest_etag VARCHAR NOT NULL,
    manifest_schema_version INTEGER,
    status VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    selected_audio_object_bucket VARCHAR,
    selected_audio_object_key VARCHAR,
    selected_audio_stream_index INTEGER,
    selected_audio_codec VARCHAR,
    selected_audio_declared_language VARCHAR,
    available_audio_tracks JSON NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_id VARCHAR
);
