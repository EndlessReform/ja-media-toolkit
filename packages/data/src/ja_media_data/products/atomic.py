"""Shared transaction mechanics for whole-replacement DuckLake products."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from collections.abc import Sequence
import uuid

import duckdb

from ja_media_data.lakehouse.time_travel import table_ref
from ja_media_data.products.materialization import (
    MaterializationContext,
    ProductCommitResult,
)


class AtomicProductStore:
    """Commit product rows and their recovery-grade materialization manifest."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def current_fingerprint(self, target: str) -> str | None:
        source = table_ref("materializations")
        row = self.connection.execute(
            """SELECT fingerprint FROM """ + source + """
               WHERE target = ? AND scope = 'corpus'
               ORDER BY computed_at DESC, materialization_id DESC LIMIT 1""",
            [target],
        ).fetchone()
        return str(row[0]) if row else None

    def replace(
        self,
        *,
        target: str,
        tables: Sequence[tuple[str, int, Sequence[tuple[object, ...]]]],
        fingerprint: str,
        rows: int,
        context: MaterializationContext,
    ) -> ProductCommitResult:
        """Atomically replace one logical product, which may span tables."""

        if self.current_fingerprint(target) == fingerprint:
            return self._validated(target, fingerprint, rows, context)
        computed_at = datetime.now(UTC)
        materialization_id = self._new_materialization_id(target)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._insert_materialization(
                materialization_id, target, fingerprint, computed_at, context,
                scope="corpus",
            )
            for table, columns, values in tables:
                self.connection.execute(f"DELETE FROM {table}")
                augmented = [row + (computed_at, context.attempt_id) for row in values]
                self._insert_many(table, columns + 2, augmented)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self._attach_snapshot(materialization_id)
        return ProductCommitResult(
            target, True, fingerprint, rows, materialization_id
        )

    def _validated(
        self,
        target: str,
        fingerprint: str,
        rows: int,
        context: MaterializationContext,
    ) -> ProductCommitResult:
        computed_at = datetime.now(UTC)
        materialization_id = self._new_materialization_id(target)
        self._insert_materialization(
            materialization_id, target, fingerprint, computed_at, context,
            scope="corpus",
        )
        self._attach_snapshot(materialization_id)
        return ProductCommitResult(
            target, False, fingerprint, rows, materialization_id
        )

    def merge(
        self,
        *,
        target: str,
        table: str,
        columns: int,
        values: Sequence[tuple[object, ...]],
        key_columns: tuple[str, ...],
        key_indexes: tuple[int, ...],
        fingerprint: str,
        context: MaterializationContext,
    ) -> ProductCommitResult:
        """Replace selected natural keys while preserving unrelated item heads."""

        if len(key_columns) != len(key_indexes):
            raise ValueError("merge key columns and indexes must have equal length")
        computed_at = datetime.now(UTC)
        materialization_id = self._new_materialization_id(target)
        predicate = " AND ".join(f"{column} = ?" for column in key_columns)
        keys = [tuple(row[index] for index in key_indexes) for row in values]
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._insert_materialization(
                materialization_id, target, fingerprint, computed_at, context,
                scope="items",
            )
            if keys:
                self.connection.executemany(
                    f"DELETE FROM {table} WHERE {predicate}", keys
                )
            augmented = [
                row + (computed_at, context.attempt_id) for row in values
            ]
            self._insert_many(table, columns + 2, augmented)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self._attach_snapshot(materialization_id)
        return ProductCommitResult(
            target, bool(values), fingerprint, len(values), materialization_id
        )

    def _insert_materialization(
        self,
        materialization_id: str,
        target: str,
        fingerprint: str,
        computed_at: datetime,
        context: MaterializationContext,
        *,
        scope: str,
    ) -> None:
        self.connection.execute(
            """INSERT INTO materializations (
                   target, scope, fingerprint, computed_at, run_id,
                   materialization_id, recipe_revision, build_key, input_heads,
                   snapshot_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            [
                target,
                scope,
                fingerprint,
                computed_at,
                context.attempt_id,
                materialization_id,
                context.recipe_revision,
                context.build_key,
                json.dumps(context.input_heads, sort_keys=True, separators=(",", ":")),
            ],
        )

    def _attach_snapshot(self, materialization_id: str) -> None:
        catalog = str(self.connection.execute("SELECT current_database()").fetchone()[0])
        quoted = '"' + catalog.replace('"', '""') + '"'
        row = self.connection.execute(
            f"SELECT id FROM {quoted}.current_snapshot()"
        ).fetchone()
        self.connection.execute(
            "UPDATE materializations SET snapshot_id = ? WHERE materialization_id = ?",
            [int(row[0]) if row else None, materialization_id],
        )

    @staticmethod
    def _new_materialization_id(target: str) -> str:
        return f"materialization-{target}-{uuid.uuid4().hex}"

    def _insert_many(
        self, table: str, columns: int, rows: Sequence[tuple[object, ...]]
    ) -> None:
        if rows:
            self.connection.executemany(
                f"INSERT INTO {table} VALUES ("
                + ", ".join("?" for _ in range(columns))
                + ")",
                rows,
            )
