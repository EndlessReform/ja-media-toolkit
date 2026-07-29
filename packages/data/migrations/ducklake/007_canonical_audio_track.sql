-- Pin the exact extracted audio object selected by canonicalization. Historical
-- rows remain readable; every materialization produced by the v2 compiler
-- populates these columns before replacing the current corpus head.

ALTER TABLE canonical_episode_inputs ADD COLUMN audio_object_bucket VARCHAR;
ALTER TABLE canonical_episode_inputs ADD COLUMN audio_object_key VARCHAR;
ALTER TABLE canonical_episode_inputs ADD COLUMN audio_stream_index INTEGER;
ALTER TABLE canonical_episode_inputs ADD COLUMN audio_codec VARCHAR;
ALTER TABLE canonical_episode_inputs ADD COLUMN audio_declared_language VARCHAR;
