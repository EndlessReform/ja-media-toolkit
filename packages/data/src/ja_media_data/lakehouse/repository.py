"""Composed DuckLake query facade and configured repository lifecycle."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Sequence

import duckdb
import psycopg

from ja_media_data.storage.binding_overrides import (
    BindingOverrideRepository,
    EffectiveBindingOverrideWriter,
    control_schema_from_settings,
)
from ja_media_data.storage.binding_schema import (
    apply_postgres_schema,
    postgres_url_for_psycopg,
)
from ja_media_data.lakehouse.catalog import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.identity_queries import IdentityQueries
from ja_media_data.products.episode_resolution.models import (
    BatchWriteResult,
    CaptureObservation,
    ReplaceResult,
    ResolutionBatch,
)
from ja_media_data.products.episode_resolution.repository import ResolutionProductStore


class DuckLakeRepository(IdentityQueries, EffectiveBindingOverrideWriter):
    """Replace automatic products and compose them with PostgreSQL overrides."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        override_repository: BindingOverrideRepository | None = None,
    ) -> None:
        super().__init__(connection, override_repository)

    def replace_bronze_captures(
        self,
        observations: Sequence[CaptureObservation],
        fingerprint: str,
        *,
        scope: str = "corpus",
        run_id: str | None = None,
    ) -> ReplaceResult:
        """Delegate the complete bronze-inventory commit to its product store."""

        return ResolutionProductStore(self.connection).replace_bronze_captures(
            observations, fingerprint, scope=scope, run_id=run_id
        )

    def replace_resolution_tables(
        self,
        batch: ResolutionBatch,
        fingerprint: str,
        *,
        scope: str = "corpus",
        run_id: str | None = None,
    ) -> BatchWriteResult:
        """Delegate the atomic three-table resolver commit to its product store."""

        return ResolutionProductStore(self.connection).replace_resolution_tables(
            batch, fingerprint, scope=scope, run_id=run_id
        )

    def flush_inlined_data(self, *, catalog_alias: str = "lakehouse") -> int:
        """Materialize a completed batch's small catalog rows as Parquet."""

        return len(
            self.connection.execute(
                f"FROM ducklake_flush_inlined_data('{catalog_alias}')"
            ).fetchall()
        )


@contextmanager
def repository_from_settings(
    *, ensure_schema: bool = True
) -> Iterator[DuckLakeRepository]:
    """Attach both stores, optionally applying schemas, and close their clients.

    Compiler and migration entrypoints retain the initializing default. Read-only
    applications must pass ``ensure_schema=False`` so observation has no DDL
    side effects.
    """

    config = CatalogConfig.from_settings()
    control_schema = control_schema_from_settings(catalog_schema=config.metadata_schema)
    with (
        connect_catalog(config, initialize_catalog=ensure_schema) as connection,
        psycopg.connect(
            postgres_url_for_psycopg(config.postgres_url), autocommit=True
        ) as control_connection,
    ):
        if ensure_schema:
            apply_schema(connection)
            apply_postgres_schema(control_connection, control_schema=control_schema)
        yield DuckLakeRepository(
            connection,
            BindingOverrideRepository(
                control_connection, control_schema=control_schema
            ),
        )
