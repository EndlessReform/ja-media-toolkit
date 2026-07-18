"""Atomic product commits plus durable global-run checkpoint metadata."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import json
from typing import Sequence
import uuid

import duckdb

from ja_media_data.execution import (
    ProductHead,
    StageExecution,
    encode_heads,
    structural_build_key,
)
from ja_media_data.pipeline_types import (
    AcceptedBinding,
    CanonicalEpisodeInput,
    CanonicalSubtitleInput,
    StageResult,
    SubtitleLanguageResult,
)
from ja_media_data.run_numbers import create_pipeline_run
from ja_media_data.lakehouse.time_travel import table_ref


class PipelineRepository:
    """Persist complete stage products and the checkpoints that produced them."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def current_head(
        self, target: str, *, snapshot_id: int | None = None
    ) -> ProductHead | None:
        """Return the newest committed metadata head for one product."""

        source = table_ref("materializations", snapshot_id=snapshot_id)
        row = self.connection.execute(
            """SELECT materialization_id, target, fingerprint, recipe_revision,
                      build_key, run_id, computed_at, snapshot_id, input_heads
               FROM """ + source + """ WHERE target = ? AND scope = 'corpus'
               ORDER BY computed_at DESC, materialization_id DESC LIMIT 1""",
            [target],
        ).fetchone()
        if row is None:
            return None
        return ProductHead(
            materialization_id=str(row[0]), target=str(row[1]),
            fingerprint=str(row[2]), recipe_revision=str(row[3]) if row[3] else None,
            build_key=str(row[4]) if row[4] else None,
            run_id=str(row[5]) if row[5] else None, computed_at=row[6],
            snapshot_id=int(row[7]) if row[7] is not None else None,
            input_heads=(json.loads(row[8]) if isinstance(row[8], str) else (row[8] or {})),
        )

    def input_heads(
        self, *targets: str, snapshot_id: int | None = None
    ) -> dict[str, object]:
        """Return compact semantic and lineage identities for declared inputs."""

        heads: dict[str, object] = {}
        for target in targets:
            head = self.current_head(target, snapshot_id=snapshot_id)
            heads[target] = (
                {"materialization_id": head.materialization_id,
                 "fingerprint": head.fingerprint}
                if head else {"materialization_id": None, "fingerprint": None}
            )
        return heads

    def start_pipeline_run(
        self, target: str, *, forced_from_stage: str | None, override_revision: int
    ) -> str:
        """Create one global dispatch whose stages may checkpoint independently."""

        return create_pipeline_run(
            self.connection, target, forced_from_stage=forced_from_stage,
            override_revision=override_revision,
        )

    def finish_pipeline_run(
        self, run_id: str, *, error: Exception | None = None
    ) -> None:
        """Close a global dispatch while preserving all successful checkpoints."""

        self.connection.execute(
            """UPDATE pipeline_runs SET status = ?, finished_at = ?,
                      terminal_snapshot_id = ?, error_message = ? WHERE run_id = ?""",
            ["failed" if error else "succeeded", datetime.now(UTC),
             self.current_snapshot_id(), str(error) if error else None, run_id],
        )

    def begin_stage(
        self, run_id: str, stage: str, ordinal: int, recipe_revision: str,
        input_heads: dict[str, object],
    ) -> StageExecution:
        """Record a running local checkpoint and return its durable identities."""

        checkpoint_id = f"checkpoint-{uuid.uuid4().hex}"
        attempt_id = f"attempt-{uuid.uuid4().hex}"
        build_key = structural_build_key(stage, recipe_revision, input_heads)
        self.connection.execute(
            """INSERT INTO run_stage_checkpoints VALUES (
                   ?, ?, ?, ?, 'running', ?, NULL, ?, ?, ?, ?, NULL, NULL)""",
            [checkpoint_id, run_id, stage, ordinal, attempt_id, recipe_revision,
             build_key, encode_heads(input_heads), datetime.now(UTC)],
        )
        return StageExecution(
            run_id, checkpoint_id, attempt_id, stage, ordinal, recipe_revision,
            build_key, input_heads,
        )

    def finish_stage(
        self, execution: StageExecution, *, disposition: str,
        materialization_id: str | None = None, error: Exception | None = None,
    ) -> None:
        """Finish one checkpoint as reused, succeeded, or failed."""

        self.connection.execute(
            """UPDATE run_stage_checkpoints SET disposition = ?,
                      materialization_id = ?, finished_at = ?, error_message = ?
               WHERE checkpoint_id = ?""",
            [disposition, materialization_id, datetime.now(UTC),
             str(error) if error else None, execution.checkpoint_id],
        )

    def replace_acceptances(
        self, rows: Sequence[AcceptedBinding], fingerprint: str,
        execution: StageExecution,
    ) -> StageResult:
        values = [tuple(asdict(item).values()) for item in rows]
        return self._replace("accepted_bindings", "accepted_bindings_auto", 9,
                             values, fingerprint, execution)

    def replace_canonical_inputs(
        self, episodes: Sequence[CanonicalEpisodeInput],
        subtitles: Sequence[CanonicalSubtitleInput], fingerprint: str,
        execution: StageExecution,
    ) -> StageResult:
        head = self.current_head("canonical_inputs")
        if head and head.fingerprint == fingerprint:
            return self._validated("canonical_inputs", fingerprint, len(episodes), execution)
        computed_at = datetime.now(UTC)
        episode_rows = [tuple(asdict(item).values()) + (computed_at, execution.attempt_id)
                        for item in episodes]
        subtitle_rows = [tuple(asdict(item).values()) + (computed_at, execution.attempt_id)
                         for item in subtitles]
        materialization_id = self._begin_product_commit(
            "canonical_inputs", fingerprint, computed_at, execution
        )
        try:
            self.connection.execute("DELETE FROM canonical_episode_inputs")
            self.connection.execute("DELETE FROM canonical_subtitle_inputs")
            self._insert_many("canonical_episode_inputs", 14, episode_rows)
            self._insert_many("canonical_subtitle_inputs", 14, subtitle_rows)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self._attach_snapshot(materialization_id)
        return _result("canonical_inputs", True, fingerprint, len(episodes),
                       execution, materialization_id)

    def replace_lid_results(
        self, rows: Sequence[SubtitleLanguageResult], fingerprint: str,
        execution: StageExecution,
    ) -> StageResult:
        values = []
        for item in rows:
            raw = asdict(item)
            raw["script_metrics"] = _json(raw["script_metrics"])
            raw["sampled_metrics"] = (
                _json(raw["sampled_metrics"]) if raw["sampled_metrics"] is not None else None
            )
            values.append(tuple(raw.values()))
        return self._replace("subtitle_lid", "subtitle_language_results", 11,
                             values, fingerprint, execution)

    def _replace(
        self, target: str, table: str, columns: int,
        rows: Sequence[tuple[object, ...]], fingerprint: str,
        execution: StageExecution,
    ) -> StageResult:
        head = self.current_head(target)
        if head and head.fingerprint == fingerprint:
            return self._validated(target, fingerprint, len(rows), execution)
        computed_at = datetime.now(UTC)
        values = [row + (computed_at, execution.attempt_id) for row in rows]
        materialization_id = self._begin_product_commit(
            target, fingerprint, computed_at, execution
        )
        try:
            self.connection.execute(f"DELETE FROM {table}")
            self._insert_many(table, columns + 2, values)
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self._attach_snapshot(materialization_id)
        return _result(target, True, fingerprint, len(rows), execution, materialization_id)

    def _validated(
        self, target: str, fingerprint: str, rows: int, execution: StageExecution
    ) -> StageResult:
        """Advance lineage after revalidation without rewriting identical data."""

        computed_at = datetime.now(UTC)
        materialization_id = self._new_materialization_id(target)
        self._insert_materialization(
            materialization_id, target, fingerprint, computed_at, execution
        )
        self._attach_snapshot(materialization_id)
        return _result(target, False, fingerprint, rows, execution, materialization_id)

    def _begin_product_commit(
        self, target: str, fingerprint: str, computed_at: datetime,
        execution: StageExecution,
    ) -> str:
        materialization_id = self._new_materialization_id(target)
        self.connection.execute("BEGIN TRANSACTION")
        self._insert_materialization(
            materialization_id, target, fingerprint, computed_at, execution
        )
        return materialization_id

    def _insert_materialization(
        self, materialization_id: str, target: str, fingerprint: str,
        computed_at: datetime, execution: StageExecution,
    ) -> None:
        self.connection.execute(
            """INSERT INTO materializations (
                   target, scope, fingerprint, computed_at, run_id,
                   materialization_id, recipe_revision, build_key, input_heads,
                   snapshot_id) VALUES (?, 'corpus', ?, ?, ?, ?, ?, ?, ?, NULL)""",
            [target, fingerprint, computed_at, execution.attempt_id,
             materialization_id, execution.recipe_revision, execution.build_key,
             encode_heads(execution.input_heads)],
        )

    def _attach_snapshot(self, materialization_id: str) -> None:
        self.connection.execute(
            "UPDATE materializations SET snapshot_id = ? WHERE materialization_id = ?",
            [self.current_snapshot_id(), materialization_id],
        )

    def current_snapshot_id(self) -> int | None:
        """Return DuckLake's current catalog snapshot for time-travel links."""

        catalog = str(self.connection.execute("SELECT current_database()").fetchone()[0])
        quoted = '"' + catalog.replace('"', '""') + '"'
        row = self.connection.execute(
            f"SELECT id FROM {quoted}.current_snapshot()"
        ).fetchone()
        return int(row[0]) if row else None

    @staticmethod
    def _new_materialization_id(target: str) -> str:
        return f"materialization-{target}-{uuid.uuid4().hex}"

    def _insert_many(
        self, table: str, columns: int, rows: Sequence[tuple[object, ...]]
    ) -> None:
        if rows:
            self.connection.executemany(
                f"INSERT INTO {table} VALUES (" + ", ".join("?" for _ in range(columns)) + ")",
                rows,
            )


def _result(
    target: str, written: bool, fingerprint: str, rows: int,
    execution: StageExecution, materialization_id: str,
) -> StageResult:
    return StageResult(
        target, written, fingerprint, rows, execution.attempt_id,
        execution.pipeline_run_id, materialization_id,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
