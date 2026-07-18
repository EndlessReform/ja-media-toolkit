"""Final Phase C2 schema tests against a PostgreSQL-backed DuckLake."""

from __future__ import annotations

import os
import uuid

import pytest

from ja_media_data.lakehouse.catalog import CatalogConfig, apply_schema, connect_catalog


def _postgres_url() -> str:
    return os.environ.get(
        "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
        "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
        "@127.0.0.1:55432/ja_media_lakehouse_test",
    )


def test_ducklake_s3_settings_can_differ_from_bronze(monkeypatch) -> None:
    monkeypatch.setenv("JA_MEDIA_DATA_DATABASE_URL", _postgres_url())
    monkeypatch.setenv("JA_MEDIA_DUCKLAKE_DATA_PATH", "s3://silver/local/")
    monkeypatch.setenv("JA_MEDIA_S3_ENDPOINT_URL", "https://garage.example")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "garage-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "garage-secret")
    monkeypatch.setenv("JA_MEDIA_DUCKLAKE_S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("JA_MEDIA_DUCKLAKE_S3_ACCESS_KEY_ID", "minio-key")
    monkeypatch.setenv("JA_MEDIA_DUCKLAKE_S3_SECRET_ACCESS_KEY", "minio-secret")

    config = CatalogConfig.from_env()

    assert config.s3_endpoint_url == "http://minio:9000"
    assert config.s3_key_id == "minio-key"
    assert config.s3_secret == "minio-secret"


def test_catalog_uses_stable_lakehouse_prefix_by_default(monkeypatch) -> None:
    monkeypatch.setenv("JA_MEDIA_DATA_DATABASE_URL", _postgres_url())
    monkeypatch.setenv("JA_MEDIA_BRONZE_BUCKET", "media")
    monkeypatch.setenv("JA_MEDIA_S3_ENDPOINT_URL", "https://garage.example")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "garage-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "garage-secret")
    monkeypatch.delenv("JA_MEDIA_DUCKLAKE_DATA_PATH", raising=False)
    monkeypatch.delenv("JA_MEDIA_DUCKLAKE_DATA_PREFIX", raising=False)
    monkeypatch.delenv("JA_MEDIA_DUCKLAKE_S3_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    config = CatalogConfig.from_env()

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
            f"deploy/lakehouse-dev ({error})"
        )
    assert apply_schema(connection) == [
        "001_identity.sql",
        "002_identity_views.sql",
            "003_phase_d_subtitle_lid.sql",
            "004_operator_runs.sql",
            "005_human_run_numbers.sql",
        ]
    yield connection
    connection.close()


def test_apply_schema_is_idempotent(catalog) -> None:
    assert apply_schema(catalog) == []
    assert catalog.execute("SELECT count(*) FROM schema_history").fetchone()[0] == 5


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
    assert catalog.execute("SELECT count(*) FROM accepted_bindings_auto").fetchone() == (0,)


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
