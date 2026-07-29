"""Final Phase C2 schema tests against a PostgreSQL-backed DuckLake."""

from __future__ import annotations

import os
import uuid

import pytest

from ja_media_data.lakehouse.catalog import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.settings import DataSettings


def _postgres_url() -> str:
    return os.environ.get(
        "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
        "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
        "@127.0.0.1:55432/ja_media_lakehouse_test",
    )


def test_ducklake_s3_settings_can_differ_from_bronze() -> None:
    settings = DataSettings(
        bronze={
            "endpoint_url": "https://garage.example",
            "bucket": "media",
            "prefix": "captures/v2",
        },
        ducklake={
            "postgres_url": _postgres_url(),
            "data_path": "s3://silver/local/",
            "s3_endpoint_url": "http://minio:9000",
            "s3_access_key_id": "minio-key",
            "s3_secret_access_key": "minio-secret",
        },
        services={"root_url": "http://services"},
    )

    config = CatalogConfig.from_settings(settings)

    assert config.s3_endpoint_url == "http://minio:9000"
    assert config.s3_key_id == "minio-key"
    assert config.s3_secret == "minio-secret"


def test_catalog_uses_toml_data_path_without_inference() -> None:
    settings = DataSettings(
        bronze={
            "endpoint_url": "https://garage.example",
            "bucket": "media",
            "prefix": "captures/v2",
        },
        ducklake={
            "postgres_url": _postgres_url(),
            "data_path": "s3://media/audio/anime/lakehouse/",
            "s3_endpoint_url": "https://garage.example",
            "s3_region": "garage",
            "s3_access_key_id": "key",
            "s3_secret_access_key": "secret",
        },
        services={"root_url": "http://services"},
    )

    config = CatalogConfig.from_settings(settings)

    assert config.data_path == "s3://media/audio/anime/lakehouse/"
    assert config.s3_region == "garage"


@pytest.fixture(scope="module")
def catalog(tmp_path_factory):
    config = CatalogConfig(
        postgres_url=_postgres_url(),
        metadata_schema="phase_c2_" + uuid.uuid4().hex,
        data_path=str(tmp_path_factory.mktemp("phase-c2-ducklake")),
    )
    try:
        connection = connect_catalog(config)
    except Exception as error:
        pytest.fail(
            "Phase C2 requires the disposable PostgreSQL fixture from "
            f"deploy/data/local ({error})"
        )
    assert apply_schema(connection) == [
        "001_identity.sql",
        "002_identity_views.sql",
        "003_phase_d_subtitle_lid.sql",
        "004_operator_runs.sql",
        "005_human_run_numbers.sql",
        "006_worker_handoffs.sql",
        "007_canonical_audio_track.sql",
        "008_capture_audio_eligibility.sql",
    ]
    yield connection
    connection.close()


def test_apply_schema_is_idempotent(catalog) -> None:
    assert apply_schema(catalog) == []
    assert catalog.execute("SELECT count(*) FROM schema_history").fetchone()[0] == 8


def test_apply_schema_rejects_an_edited_applied_file(catalog, tmp_path) -> None:
    (tmp_path / "001_identity.sql").write_text("SELECT 'changed';")

    with pytest.raises(RuntimeError, match="applied schema file changed"):
        apply_schema(catalog, tmp_path)


def test_only_final_resolution_contracts_exist(catalog) -> None:
    tables = {
        row[0]
        for row in catalog.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    }
    assert "episode_hints_auto" in tables
    assert "episode_binding_proposals" in tables
    assert "accepted_bindings_auto" in tables
    assert "capture_audio_eligibility" in tables
    assert "resolution_issues_auto" in tables
    assert "episode_hints" not in tables
    assert "episode_bindings" not in tables
    assert "episode_resolution_issues" not in tables


def test_resolution_bindings_are_proposals_until_acceptance(catalog) -> None:
    catalog.execute(
        """INSERT INTO episode_binding_proposals VALUES
           ('binding-1', 'anilist', '100', '1', 'capture-1', 'automatic', '{}',
            'etag-1', 'resolver-v1', '2026-01-01 00:00:00+00', 'test')"""
    )
    assert catalog.execute(
        "SELECT count(*) FROM accepted_bindings_auto"
    ).fetchone() == (0,)


def test_findings_detect_missing_capture_and_duplicate_auto_output(catalog) -> None:
    catalog.execute(
        """INSERT INTO episode_binding_proposals VALUES
           ('binding-2', 'anilist', '100', '1', 'capture-2', 'automatic', '{}',
            'etag-1', 'resolver-v1', '2026-01-01 00:00:00+00', 'test')"""
    )
    findings = catalog.execute(
        "SELECT finding_type FROM consistency_findings ORDER BY finding_type"
    ).fetchall()
    assert findings == [
        ("proposal_locator_collision",),
        ("proposal_missing_capture",),
        ("proposal_missing_capture",),
    ]
