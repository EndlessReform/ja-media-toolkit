-- Phase D vertical slice. Resolver output is a proposal, acceptance is an
-- explicit replaceable policy product, and only canonical accepted inputs may
-- feed subtitle language identification.

ALTER TABLE bronze_captures ADD COLUMN manifest_modified_at TIMESTAMPTZ;
DROP VIEW current_bindings;
DROP VIEW consistency_findings;
ALTER TABLE episode_bindings_auto RENAME TO episode_binding_proposals;
ALTER TABLE episode_binding_proposals RENAME binding_id TO proposal_id;
ALTER TABLE episode_binding_proposals RENAME decision_method TO proposal_method;
ALTER TABLE episode_binding_proposals RENAME decision_evidence TO proposal_evidence;

CREATE VIEW consistency_findings AS
WITH locator_duplicates AS (
    SELECT
        'proposal_locator_collision' AS finding_type,
        'locator' AS subject_type,
        namespace || ':' || series_id || ':' || episode AS subject_id,
        string_agg(proposal_id, ',' ORDER BY proposal_id) AS related_ids,
        count(*)::BIGINT AS finding_count
    FROM episode_binding_proposals
    GROUP BY namespace, series_id, episode
    HAVING count(*) > 1
),
capture_duplicates AS (
    SELECT
        'proposal_capture_collision' AS finding_type,
        'audio_capture' AS subject_type,
        audio_capture_id AS subject_id,
        string_agg(proposal_id, ',' ORDER BY proposal_id) AS related_ids,
        count(*)::BIGINT AS finding_count
    FROM episode_binding_proposals
    GROUP BY audio_capture_id
    HAVING count(*) > 1
),
missing_captures AS (
    SELECT
        'proposal_missing_capture' AS finding_type,
        'binding_proposal' AS subject_type,
        proposal_id AS subject_id,
        audio_capture_id AS related_ids,
        1::BIGINT AS finding_count
    FROM episode_binding_proposals AS proposal
    WHERE NOT EXISTS (
        SELECT 1 FROM bronze_captures AS capture
        WHERE capture.capture_id = proposal.audio_capture_id
    )
),
capture_identity_moves AS (
    SELECT
        'bronze_capture_identity_collision' AS finding_type,
        'audio_capture' AS subject_type,
        capture_id AS subject_id,
        string_agg(
            manifest_bucket || '/' || manifest_key,
            ',' ORDER BY manifest_bucket, manifest_key
        ) AS related_ids,
        count(*)::BIGINT AS finding_count
    FROM bronze_captures
    GROUP BY capture_id
    HAVING count(DISTINCT manifest_bucket || '/' || manifest_key) > 1
)
SELECT * FROM locator_duplicates
UNION ALL SELECT * FROM capture_duplicates
UNION ALL SELECT * FROM missing_captures
UNION ALL SELECT * FROM capture_identity_moves;

CREATE TABLE accepted_bindings_auto (
    acceptance_id VARCHAR,
    proposal_id VARCHAR NOT NULL,
    namespace VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    episode VARCHAR NOT NULL,
    audio_capture_id VARCHAR NOT NULL,
    acceptance_method VARCHAR NOT NULL,
    policy_version VARCHAR NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_id VARCHAR
);

CREATE TABLE canonical_episode_inputs (
    canonical_id VARCHAR,
    namespace VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    episode VARCHAR NOT NULL,
    audio_capture_id VARCHAR NOT NULL,
    binding_id VARCHAR NOT NULL,
    binding_source VARCHAR NOT NULL,
    manifest_bucket VARCHAR NOT NULL,
    manifest_key VARCHAR NOT NULL,
    manifest_etag VARCHAR NOT NULL,
    manifest_modified_at TIMESTAMPTZ NOT NULL,
    input_fingerprint VARCHAR NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_id VARCHAR
);

CREATE TABLE canonical_subtitle_inputs (
    subtitle_input_id VARCHAR,
    canonical_id VARCHAR NOT NULL,
    namespace VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    episode VARCHAR NOT NULL,
    audio_capture_id VARCHAR NOT NULL,
    object_bucket VARCHAR NOT NULL,
    object_key VARCHAR NOT NULL,
    stream_index INTEGER NOT NULL,
    codec VARCHAR,
    declared_language VARCHAR,
    input_fingerprint VARCHAR NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_id VARCHAR
);

CREATE TABLE subtitle_language_results (
    subtitle_input_id VARCHAR,
    namespace VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    episode VARCHAR NOT NULL,
    audio_capture_id VARCHAR NOT NULL,
    language VARCHAR NOT NULL,
    reason VARCHAR NOT NULL,
    script_metrics JSON NOT NULL,
    sampled_metrics JSON,
    input_fingerprint VARCHAR NOT NULL,
    recipe_version VARCHAR NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL,
    run_id VARCHAR NOT NULL
);
