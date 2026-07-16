"""Batch persistence for rebuildable DuckLake identity products.

Automatic capture and resolution tables are compiled products, not ledgers.
Writers validate a complete batch in memory and replace its tables in one
DuckLake transaction. Human binding decisions remain in ordinary PostgreSQL.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator, Sequence

import duckdb
import psycopg

from ja_media_data.binding_overrides import (
    BindingOverrideRepository,
    EffectiveBindingOverrideWriter,
    apply_postgres_schema,
    control_schema_from_env,
    postgres_url_for_psycopg,
)
from ja_media_data.lakehouse.catalog import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.identity_queries import IdentityQueries
from ja_media_data.resolution_types import (
    BatchWriteResult,
    CaptureObservation,
    ReplaceResult,
    ResolutionBatch,
)


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
        """Replace the rebuildable capture cache with one complete scan."""

        rows = [
            (
                item.capture_id,
                item.series_namespace,
                item.series_id,
                item.manifest_bucket,
                item.manifest_key,
                item.manifest_etag,
                item.manifest_schema_version,
                item.observed_at,
                item.observed_at,
            )
            for item in observations
        ]
        if self._fingerprint_matches("bronze_captures", scope, fingerprint):
            return ReplaceResult(False, fingerprint, len(rows))
        computed_at = datetime.now(UTC)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute("DELETE FROM bronze_captures")
            if rows:
                self.connection.executemany(
                    "INSERT INTO bronze_captures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            self._record_materialization(
                "bronze_captures", scope, fingerprint, computed_at, run_id
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return ReplaceResult(True, fingerprint, len(rows))

    def replace_resolution_tables(
        self,
        batch: ResolutionBatch,
        fingerprint: str,
        *,
        scope: str = "corpus",
        run_id: str | None = None,
    ) -> BatchWriteResult:
        """Replace all automatic resolver tables unless the fingerprint matches."""

        _validate_resolution_batch(batch)
        counts = (len(batch.hints), len(batch.bindings), len(batch.issues))
        if self._fingerprint_matches("episode_resolution", scope, fingerprint):
            return BatchWriteResult(False, fingerprint, *counts)

        computed_at = datetime.now(UTC)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._replace_hints(batch, computed_at)
            self._replace_bindings(batch, computed_at)
            self._replace_issues(batch, computed_at)
            self._record_materialization(
                "episode_resolution", scope, fingerprint, computed_at, run_id
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return BatchWriteResult(True, fingerprint, *counts)

    def _fingerprint_matches(
        self, target: str, scope: str, fingerprint: str
    ) -> bool:
        row = self.connection.execute(
            """SELECT fingerprint FROM materializations
               WHERE target = ? AND scope = ?
               ORDER BY computed_at DESC LIMIT 1""",
            [target, scope],
        ).fetchone()
        return bool(row and row[0] == fingerprint)

    def _record_materialization(
        self,
        target: str,
        scope: str,
        fingerprint: str,
        computed_at: datetime,
        run_id: str | None,
    ) -> None:
        self.connection.execute(
            "DELETE FROM materializations WHERE target = ? AND scope = ?",
            [target, scope],
        )
        self.connection.execute(
            "INSERT INTO materializations VALUES (?, ?, ?, ?, ?)",
            [target, scope, fingerprint, computed_at, run_id],
        )

    def _replace_hints(self, batch: ResolutionBatch, computed_at: datetime) -> None:
        self.connection.execute("DELETE FROM episode_hints_auto")
        rows = [
            (
                item.hint_id,
                item.capture_id,
                item.candidate_namespace,
                item.candidate_series_id,
                item.candidate_episode,
                item.method,
                item.confidence,
                _json(item.evidence),
                item.input_data_version,
                item.recipe_version,
                computed_at,
                item.run_source,
            )
            for item in batch.hints
        ]
        if rows:
            self.connection.executemany(
                "INSERT INTO episode_hints_auto VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def _replace_bindings(
        self, batch: ResolutionBatch, computed_at: datetime
    ) -> None:
        self.connection.execute("DELETE FROM episode_bindings_auto")
        rows = [
            (
                item.binding_id,
                item.namespace,
                item.series_id,
                item.episode,
                item.audio_capture_id,
                item.decision_method,
                _json(item.decision_evidence),
                item.input_data_version,
                item.recipe_version,
                computed_at,
                item.run_source,
            )
            for item in batch.bindings
        ]
        if rows:
            self.connection.executemany(
                "INSERT INTO episode_bindings_auto VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def _replace_issues(self, batch: ResolutionBatch, computed_at: datetime) -> None:
        self.connection.execute("DELETE FROM resolution_issues_auto")
        rows = [
            (
                item.issue_id,
                item.capture_id,
                item.hint_id,
                item.kind,
                _json(item.details),
                computed_at,
                item.run_source,
            )
            for item in batch.issues
        ]
        if rows:
            self.connection.executemany(
                "INSERT INTO resolution_issues_auto VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def flush_inlined_data(self, *, catalog_alias: str = "lakehouse") -> int:
        """Materialize a completed batch's small catalog rows as Parquet."""

        return len(
            self.connection.execute(
                f"FROM ducklake_flush_inlined_data('{catalog_alias}')"
            ).fetchall()
        )


def _validate_resolution_batch(batch: ResolutionBatch) -> None:
    _require_unique("hint IDs", [item.hint_id for item in batch.hints])
    _require_unique("binding IDs", [item.binding_id for item in batch.bindings])
    _require_unique("issue IDs", [item.issue_id for item in batch.issues])
    _require_unique(
        "automatic locators",
        [(item.namespace, item.series_id, item.episode) for item in batch.bindings],
    )
    _require_unique(
        "automatic captures", [item.audio_capture_id for item in batch.bindings]
    )


def _require_unique(label: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"resolution batch contains duplicate {label}")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@contextmanager
def repository_from_env() -> Iterator[DuckLakeRepository]:
    """Attach both stores, apply reviewed schemas, and close their clients."""

    config = CatalogConfig.from_env()
    control_schema = control_schema_from_env(catalog_schema=config.metadata_schema)
    with connect_catalog(config) as connection, psycopg.connect(
        postgres_url_for_psycopg(config.postgres_url), autocommit=True
    ) as control_connection:
        apply_schema(connection)
        apply_postgres_schema(control_connection, control_schema=control_schema)
        yield DuckLakeRepository(
            connection,
            BindingOverrideRepository(
                control_connection, control_schema=control_schema
            ),
        )
