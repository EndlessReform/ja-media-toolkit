-- Automatic bindings are current by construction. PostgreSQL overrides are
-- composed over this view by the Python query layer.

CREATE VIEW current_bindings AS
SELECT
    namespace,
    series_id,
    episode,
    binding_id,
    audio_capture_id,
    decision_method,
    decision_evidence,
    input_data_version,
    recipe_version,
    computed_at,
    run_source
FROM episode_bindings_auto;

CREATE VIEW consistency_findings AS
WITH locator_duplicates AS (
    SELECT
        'automatic_locator_collision' AS finding_type,
        'locator' AS subject_type,
        namespace || ':' || series_id || ':' || episode AS subject_id,
        string_agg(binding_id, ',' ORDER BY binding_id) AS related_ids,
        count(*)::BIGINT AS finding_count
    FROM episode_bindings_auto
    GROUP BY namespace, series_id, episode
    HAVING count(*) > 1
),
capture_duplicates AS (
    SELECT
        'automatic_capture_collision' AS finding_type,
        'audio_capture' AS subject_type,
        audio_capture_id AS subject_id,
        string_agg(binding_id, ',' ORDER BY binding_id) AS related_ids,
        count(*)::BIGINT AS finding_count
    FROM episode_bindings_auto
    GROUP BY audio_capture_id
    HAVING count(*) > 1
),
missing_captures AS (
    SELECT
        'automatic_binding_missing_capture' AS finding_type,
        'binding' AS subject_type,
        binding_id AS subject_id,
        audio_capture_id AS related_ids,
        1::BIGINT AS finding_count
    FROM episode_bindings_auto AS binding
    WHERE NOT EXISTS (
        SELECT 1 FROM bronze_captures AS capture
        WHERE capture.capture_id = binding.audio_capture_id
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
