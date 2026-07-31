"""Atomic persistence for bronze inventory and automatic resolution products."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
import json
import uuid

import duckdb

from ja_media_data.products.episode_resolution.models import (
    BatchWriteResult,
    CaptureObservation,
    ReplaceResult,
    ResolutionBatch,
)


class ResolutionProductStore:
    """Commit complete resolver collections without execution callbacks."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self.connection = connection

    def replace_bronze_captures(
        self,
        observations: Sequence[CaptureObservation],
        fingerprint: str,
        *,
        scope: str,
        run_id: str | None,
    ) -> ReplaceResult:
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
                item.manifest_modified_at,
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
                    "INSERT INTO bronze_captures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            materialization_id = self._record_materialization(
                "bronze_captures", scope, fingerprint, computed_at, run_id
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self._attach_snapshot(materialization_id)
        return ReplaceResult(True, fingerprint, len(rows))

    def replace_resolution_tables(
        self,
        batch: ResolutionBatch,
        fingerprint: str,
        *,
        scope: str,
        run_id: str | None,
    ) -> BatchWriteResult:
        _validate_resolution_batch(batch)
        counts = (len(batch.hints), len(batch.proposals), len(batch.issues))
        if self._fingerprint_matches("episode_resolution", scope, fingerprint):
            return BatchWriteResult(False, fingerprint, *counts)
        computed_at = datetime.now(UTC)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._replace_hints(batch, computed_at)
            self._replace_proposals(batch, computed_at)
            self._replace_issues(batch, computed_at)
            materialization_id = self._record_materialization(
                "episode_resolution", scope, fingerprint, computed_at, run_id
            )
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self._attach_snapshot(materialization_id)
        return BatchWriteResult(True, fingerprint, *counts)

    def _fingerprint_matches(self, target: str, scope: str, fingerprint: str) -> bool:
        row = self.connection.execute(
            """SELECT fingerprint FROM materializations
               WHERE target = ? AND scope = ?
               ORDER BY computed_at DESC, materialization_id DESC LIMIT 1""",
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
    ) -> str:
        materialization_id = f"materialization-{target}-{uuid.uuid4().hex}"
        self.connection.execute(
            """INSERT INTO materializations (
                   target, scope, fingerprint, computed_at, run_id,
                   materialization_id, recipe_revision, build_key, input_heads,
                   snapshot_id) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, '{}', NULL)""",
            [target, scope, fingerprint, computed_at, run_id, materialization_id],
        )
        return materialization_id

    def _attach_snapshot(self, materialization_id: str) -> None:
        catalog = str(
            self.connection.execute("SELECT current_database()").fetchone()[0]
        )
        quoted = '"' + catalog.replace('"', '""') + '"'
        snapshot = self.connection.execute(
            f"SELECT id FROM {quoted}.current_snapshot()"
        ).fetchone()
        self.connection.execute(
            "UPDATE materializations SET snapshot_id = ? WHERE materialization_id = ?",
            [int(snapshot[0]) if snapshot else None, materialization_id],
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

    def _replace_proposals(self, batch: ResolutionBatch, computed_at: datetime) -> None:
        self.connection.execute("DELETE FROM episode_binding_proposals")
        rows = [
            (
                item.proposal_id,
                item.namespace,
                item.series_id,
                item.episode,
                item.audio_capture_id,
                item.proposal_method,
                _json(item.proposal_evidence),
                item.input_data_version,
                item.recipe_version,
                computed_at,
                item.run_source,
            )
            for item in batch.proposals
        ]
        if rows:
            self.connection.executemany(
                "INSERT INTO episode_binding_proposals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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


def _validate_resolution_batch(batch: ResolutionBatch) -> None:
    _require_unique("hint IDs", [item.hint_id for item in batch.hints])
    _require_unique("proposal IDs", [item.proposal_id for item in batch.proposals])
    _require_unique("issue IDs", [item.issue_id for item in batch.issues])
    _require_unique(
        "proposed captures", [item.audio_capture_id for item in batch.proposals]
    )


def _require_unique(label: str, values: Sequence[object]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"resolution batch contains duplicate {label}")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
