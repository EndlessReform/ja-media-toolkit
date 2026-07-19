"""Shared data-package integration fixtures."""

from __future__ import annotations

import os
import uuid

import pytest

from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.repository import DuckLakeRepository


@pytest.fixture
def repository(tmp_path) -> DuckLakeRepository:
    """Create an isolated local DuckLake catalog for cross-module tests."""

    postgres_url = os.environ.get(
        "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
        "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
        "@127.0.0.1:55432/ja_media_lakehouse_test",
    )
    connection = connect_catalog(
        CatalogConfig(
            postgres_url=postgres_url,
            metadata_schema="data_test_" + uuid.uuid4().hex,
            data_path=str(tmp_path / "ducklake"),
        )
    )
    apply_schema(connection)
    yield DuckLakeRepository(connection)
    connection.close()
