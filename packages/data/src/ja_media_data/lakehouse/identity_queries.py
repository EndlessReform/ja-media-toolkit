"""Read projections over the DuckLake episode-identity contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from ja_media_data.binding_overrides import (
        BindingOverrideRecord,
        BindingOverrideRepository,
    )


@dataclass(frozen=True)
class CaptureRecord:
    """The indexed fields needed to retrieve one immutable bronze manifest."""

    capture_id: str
    manifest_bucket: str
    manifest_key: str
    manifest_etag: str


@dataclass(frozen=True)
class BindingRecord:
    """One effective automatic or PostgreSQL-override binding."""

    binding_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str


@dataclass(frozen=True)
class IssueRecord:
    """One inspectable resolution issue."""

    issue_id: str
    capture_id: str
    hint_id: str | None
    kind: str
    details: dict[str, object]
    status: str


@dataclass(frozen=True)
class ConsistencyFinding:
    """One automatic or cross-store data-quality finding."""

    finding_type: str
    subject_type: str
    subject_id: str
    related_ids: str | None
    finding_count: int


class IdentityQueries:
    """Reusable current-state and diagnostic queries for CLI and resolver code."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        override_repository: BindingOverrideRepository | None = None,
    ) -> None:
        self.connection = connection
        self.override_repository = override_repository

    def get_capture(self, capture_id: str) -> CaptureRecord | None:
        row = self.connection.execute(
            """SELECT capture_id, manifest_bucket, manifest_key, manifest_etag
               FROM bronze_captures WHERE capture_id = ? LIMIT 1""",
            [capture_id],
        ).fetchone()
        return CaptureRecord(*row) if row else None

    def get_current_binding_for_capture(self, capture_id: str) -> BindingRecord | None:
        if self.override_repository is not None:
            override = self.override_repository.get_current_override_for_capture(
                capture_id
            )
            if override is not None:
                return _binding_from_override(override)
        row = self.connection.execute(
            """SELECT binding_id, namespace, series_id, episode, audio_capture_id
               FROM current_bindings WHERE audio_capture_id = ?
               LIMIT 1""",
            [capture_id],
        ).fetchone()
        if not row:
            return None
        binding = BindingRecord(*row)
        if self.override_repository is not None:
            override = self.override_repository.get_current_override(
                binding.namespace, binding.series_id, binding.episode
            )
            if override is not None:
                return None
        return binding

    def get_current_binding(
        self, namespace: str, series_id: str, episode: str
    ) -> BindingRecord | None:
        if self.override_repository is not None:
            override = self.override_repository.get_current_override(
                namespace, series_id, episode
            )
            if override is not None:
                return (
                    _binding_from_override(override)
                    if override.audio_capture_id is not None
                    else None
                )
        row = self.connection.execute(
            """SELECT binding_id, namespace, series_id, episode, audio_capture_id
               FROM current_bindings
               WHERE namespace = ? AND series_id = ? AND episode = ?""",
            [namespace, series_id, episode],
        ).fetchone()
        return BindingRecord(*row) if row else None

    def get_latest_open_issue(self, capture_id: str) -> IssueRecord | None:
        if self.get_current_binding_for_capture(capture_id) is not None:
            return None
        row = self.connection.execute(
            """SELECT issue_id, capture_id, hint_id, kind, details, 'open'
               FROM resolution_issues_auto
               WHERE capture_id = ?
               ORDER BY computed_at DESC, issue_id DESC LIMIT 1""",
            [capture_id],
        ).fetchone()
        if not row:
            return None
        return IssueRecord(*row[:4], json.loads(row[4]), row[5])

    def list_open_issues(self, *, limit: int = 100) -> list[IssueRecord]:
        rows = self.connection.execute(
            """SELECT issue_id, capture_id, hint_id, kind, details, 'open'
               FROM resolution_issues_auto
               ORDER BY computed_at DESC, issue_id DESC LIMIT ?""",
            [limit],
        ).fetchall()
        issues = [IssueRecord(*row[:4], json.loads(row[4]), row[5]) for row in rows]
        return [
            issue
            for issue in issues
            if self.get_current_binding_for_capture(issue.capture_id) is None
        ][:limit]

    def list_unresolved_capture_ids(self, *, limit: int = 100) -> list[str]:
        rows = self.connection.execute(
            "SELECT capture_id FROM bronze_captures ORDER BY capture_id",
        ).fetchall()
        return [
            row[0]
            for row in rows
            if self.get_current_binding_for_capture(row[0]) is None
        ][:limit]

    def list_consistency_findings(self) -> list[ConsistencyFinding]:
        """Compose DuckLake checks with PostgreSQL override reference checks."""

        rows = self.connection.execute(
            """SELECT finding_type, subject_type, subject_id,
                      related_ids, finding_count
               FROM consistency_findings
               ORDER BY finding_type, subject_id"""
        ).fetchall()
        findings = [ConsistencyFinding(*row) for row in rows]
        if self.override_repository is None:
            return findings
        for override in self.override_repository.iter_current_overrides():
            capture_id = override.audio_capture_id
            if capture_id is None:
                continue
            if self.get_capture(capture_id) is None:
                findings.append(
                    ConsistencyFinding(
                        "override_missing_capture",
                        "binding_override",
                        override.override_id,
                        capture_id,
                        1,
                    )
                )
                continue
            automatic = self.connection.execute(
                """SELECT namespace, series_id, episode, binding_id
                   FROM current_bindings WHERE audio_capture_id = ? LIMIT 1""",
                [capture_id],
            ).fetchone()
            if automatic is None or automatic[:3] == (
                override.namespace,
                override.series_id,
                override.episode,
            ):
                continue
            if self.override_repository.get_current_override(*automatic[:3]) is None:
                findings.append(
                    ConsistencyFinding(
                        "override_automatic_capture_collision",
                        "audio_capture",
                        capture_id,
                        f"{override.override_id},{automatic[3]}",
                        2,
                    )
                )
        return sorted(findings, key=lambda item: (item.finding_type, item.subject_id))

    def summary(self) -> dict[str, int]:
        """Return bounded operator counts without exposing row contents."""

        names = (
            "bronze_captures",
            "episode_hints_auto",
            "episode_bindings_auto",
            "resolution_issues_auto",
            "consistency_findings",
        )
        result = {
            name: self.connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
            for name in names
        }
        if self.override_repository is None:
            result["current_bindings"] = self.connection.execute(
                "SELECT count(*) FROM current_bindings"
            ).fetchone()[0]
            return result
        overrides = tuple(self.override_repository.iter_current_overrides())
        overridden_locators = {
            (item.namespace, item.series_id, item.episode) for item in overrides
        }
        automatic = self.connection.execute(
            "SELECT namespace, series_id, episode FROM current_bindings"
        ).fetchall()
        result["binding_overrides"] = len(overrides)
        result["consistency_findings"] = len(self.list_consistency_findings())
        result["current_bindings"] = sum(
            tuple(row) not in overridden_locators for row in automatic
        ) + sum(item.audio_capture_id is not None for item in overrides)
        return result


def _binding_from_override(override: BindingOverrideRecord) -> BindingRecord:
    """Adapt the PostgreSQL decision record to the shared read contract."""

    return BindingRecord(
        binding_id=override.override_id,
        namespace=override.namespace,
        series_id=override.series_id,
        episode=override.episode,
        audio_capture_id=override.audio_capture_id,
    )
