"""Opt-in Phase B smoke test for the disposable MinIO data path."""

from __future__ import annotations

import os
import uuid

import pytest

from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog


pytestmark = pytest.mark.skipif(
    os.environ.get("JA_MEDIA_PHASE_B_MINIO_SMOKE") != "1",
    reason="set JA_MEDIA_PHASE_B_MINIO_SMOKE=1 to exercise local MinIO",
)


def test_schema_and_parquet_round_trip_through_minio() -> None:
    token = uuid.uuid4().hex
    config = CatalogConfig(
        postgres_url=os.environ.get(
            "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
            "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
            "@127.0.0.1:55432/ja_media_lakehouse_test",
        ),
        metadata_schema=f"phase_b_minio_{token}",
        data_path=f"s3://ja-media-lakehouse-test/ducklake/tests/{token}/",
        s3_endpoint_url="http://127.0.0.1:59000",
        s3_region="us-east-1",
        s3_key_id="ja_media_lakehouse_test",
        s3_secret="ja-media-lakehouse-test-only",
    )
    with connect_catalog(config) as connection:
        apply_schema(connection)
        connection.execute(
            """INSERT INTO bronze_captures VALUES
               ('minio-capture', 'fixture', '1', 'fixture', 'manifest.json',
                'etag', 1, '2026-01-01 00:00:00+00', '2026-01-01 00:00:00+00')"""
        )
        flushed = connection.execute(
            "FROM ducklake_flush_inlined_data('lakehouse', table_name => 'bronze_captures')"
        ).fetchall()
        assert flushed
        assert connection.execute(
            "SELECT capture_id FROM bronze_captures"
        ).fetchone()[0] == "minio-capture"
