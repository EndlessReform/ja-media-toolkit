"""Real, bounded result projections loaded only when an operator selects a stage."""

import json

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.lakehouse.time_travel import table_ref
from ja_media_data.operator.models import (
    AcceptanceObservation,
    AudioEligibilityObservation,
    CanonicalInputObservation,
    ResolutionIssueObservation,
    StageResultPage,
)


class StageResultProjector:
    """Query stage-owned result rows with a hard display-page ceiling."""

    def __init__(self, repository: DuckLakeRepository) -> None:
        self.repository = repository

    def page(
        self,
        stage: str,
        *,
        series_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
        snapshot_id: int | None = None,
    ) -> StageResultPage:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError(
                "stage result pages require offset >= 0 and 1 <= limit <= 100"
            )
        projectors = {
            "episode_resolution": self._resolution,
            "audio_eligibility": self._audio_eligibility,
            "accepted_bindings": self._acceptances,
            "canonical_inputs": self._canonical,
        }
        try:
            return projectors[stage](
                series_id=series_id,
                offset=offset,
                limit=limit,
                snapshot_id=snapshot_id,
            )
        except KeyError as error:
            raise KeyError(stage) from error

    def _resolution(
        self,
        *,
        series_id: str | None,
        offset: int,
        limit: int,
        snapshot_id: int | None,
    ) -> StageResultPage:
        where, params = _series_filter(series_id, "capture")
        issues = table_ref(
            "resolution_issues_auto", snapshot_id=snapshot_id, alias="issue"
        )
        captures = table_ref(
            "bronze_captures", snapshot_id=snapshot_id, alias="capture"
        )
        source = (
            f" FROM {issues} JOIN {captures} ON capture.capture_id = issue.capture_id"
        )
        total = self.repository.connection.execute(
            "SELECT count(*)" + source + where, params
        ).fetchone()[0]
        rows = self.repository.connection.execute(
            """SELECT issue.issue_id, issue.capture_id, issue.hint_id, issue.kind,
                      issue.details, capture.series_namespace, capture.series_id,
                      capture.manifest_key"""
            + source
            + where
            + " ORDER BY capture.series_id, capture.manifest_key, issue.issue_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = tuple(
            ResolutionIssueObservation(
                issue_id=str(row[0]),
                capture_id=str(row[1]),
                hint_id=str(row[2]) if row[2] else None,
                kind=str(row[3]),
                details_json=_details_json(row[4]),
                namespace=str(row[5]),
                series_id=str(row[6]),
                manifest_key=str(row[7]),
            )
            for row in rows
        )
        return StageResultPage(
            stage="episode_resolution",
            label="Resolver Proposals · Exceptions",
            items=items,
            total=int(total),
            offset=offset,
            limit=limit,
        )

    def _acceptances(
        self,
        *,
        series_id: str | None,
        offset: int,
        limit: int,
        snapshot_id: int | None,
    ) -> StageResultPage:
        where, params = _series_filter(series_id, "accepted")
        accepted = table_ref(
            "accepted_bindings_auto", snapshot_id=snapshot_id, alias="accepted"
        )
        captures = table_ref(
            "bronze_captures", snapshot_id=snapshot_id, alias="capture"
        )
        source = f" FROM {accepted} JOIN {captures} ON capture.capture_id = accepted.audio_capture_id"
        total = self.repository.connection.execute(
            "SELECT count(*)" + source + where, params
        ).fetchone()[0]
        rows = self.repository.connection.execute(
            """SELECT accepted.acceptance_id, accepted.proposal_id, accepted.namespace,
                      accepted.series_id, accepted.episode, accepted.audio_capture_id,
                      accepted.acceptance_method, accepted.policy_version, accepted.computed_at,
                      capture.manifest_bucket, capture.manifest_key"""
            + source
            + where
            + " ORDER BY accepted.series_id, try_cast(accepted.episode AS INTEGER), accepted.episode LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = tuple(
            AcceptanceObservation(
                acceptance_id=str(row[0]),
                proposal_id=str(row[1]),
                namespace=str(row[2]),
                series_id=str(row[3]),
                episode=str(row[4]),
                capture_id=str(row[5]),
                method=str(row[6]),
                policy_version=str(row[7]),
                computed_at=row[8],
                manifest_url=f"s3://{row[9]}/{row[10]}",
            )
            for row in rows
        )
        return StageResultPage(
            stage="accepted_bindings",
            label="Automatic Acceptance · Accepted",
            items=items,
            total=int(total),
            offset=offset,
            limit=limit,
        )

    def _audio_eligibility(
        self,
        *,
        series_id: str | None,
        offset: int,
        limit: int,
        snapshot_id: int | None,
    ) -> StageResultPage:
        where, params = _series_filter(series_id, "capture")
        decisions = table_ref(
            "capture_audio_eligibility", snapshot_id=snapshot_id, alias="audio"
        )
        captures = table_ref(
            "bronze_captures", snapshot_id=snapshot_id, alias="capture"
        )
        source = (
            f" FROM {decisions} JOIN {captures} "
            "ON capture.capture_id = audio.capture_id"
        )
        total = self.repository.connection.execute(
            "SELECT count(*)" + source + where, params
        ).fetchone()[0]
        rows = self.repository.connection.execute(
            """SELECT audio.capture_id, capture.series_namespace,
                      capture.series_id, audio.status, audio.reason,
                      audio.selected_audio_stream_index,
                      audio.selected_audio_declared_language,
                      audio.manifest_bucket, audio.manifest_key,
                      audio.available_audio_tracks, audio.computed_at"""
            + source
            + where
            + " ORDER BY audio.status DESC, capture.series_id, audio.manifest_key"
            " LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = tuple(
            AudioEligibilityObservation(
                capture_id=str(row[0]),
                namespace=str(row[1]),
                series_id=str(row[2]),
                status=str(row[3]),
                reason=str(row[4]),
                selected_stream_index=int(row[5]) if row[5] is not None else None,
                selected_language=str(row[6]) if row[6] else None,
                manifest_url=f"s3://{row[7]}/{row[8]}",
                available_tracks_json=_details_json(row[9]),
                computed_at=row[10],
            )
            for row in rows
        )
        return StageResultPage(
            stage="audio_eligibility",
            label="Audio Eligibility · Decisions",
            items=items,
            total=int(total),
            offset=offset,
            limit=limit,
        )

    def _canonical(
        self,
        *,
        series_id: str | None,
        offset: int,
        limit: int,
        snapshot_id: int | None,
    ) -> StageResultPage:
        where, params = _series_filter(series_id, "canonical")
        source = " FROM " + table_ref(
            "canonical_episode_inputs", snapshot_id=snapshot_id, alias="canonical"
        )
        total = self.repository.connection.execute(
            "SELECT count(*)" + source + where, params
        ).fetchone()[0]
        rows = self.repository.connection.execute(
            """SELECT canonical_id, namespace, series_id, episode, audio_capture_id,
                      binding_source, manifest_bucket, manifest_key, computed_at"""
            + source
            + where
            + " ORDER BY series_id, try_cast(episode AS INTEGER), episode LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        items = tuple(
            CanonicalInputObservation(
                canonical_id=str(row[0]),
                namespace=str(row[1]),
                series_id=str(row[2]),
                episode=str(row[3]),
                capture_id=str(row[4]),
                binding_source=str(row[5]),
                manifest_url=f"s3://{row[6]}/{row[7]}",
                computed_at=row[8],
            )
            for row in rows
        )
        return StageResultPage(
            stage="canonical_inputs",
            label="Canonical Inputs · Selected",
            items=items,
            total=int(total),
            offset=offset,
            limit=limit,
        )


def _series_filter(series_id: str | None, alias: str) -> tuple[str, list[object]]:
    return (f" WHERE {alias}.series_id = ?", [series_id]) if series_id else ("", [])


def _details_json(value: object) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
