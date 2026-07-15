"""Focused transactional operations over the episode-identity ledger."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ja_media_data.ledger_types import (
    BindingDecision,
    CaptureObservation,
    HintClaim,
    ResolutionIssueClaim,
)
from ja_media_data.models import (
    BronzeCapture,
    CurrentEpisodeBinding,
    EpisodeBinding,
    EpisodeHint,
    EpisodeResolutionIssue,
)


class CaptureIdentityConflictError(ValueError):
    """A capture ID was observed at a different immutable manifest location."""


class BindingConflictError(ValueError):
    """A locator or capture already has a different current binding."""


class LedgerRepository:
    """Small repository for indexed reads and invariant-bearing writes."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def observe_capture(self, observation: CaptureObservation) -> None:
        """Insert or refresh a rebuildable capture header idempotently."""

        with self._sessions.begin() as session:
            capture = session.get(BronzeCapture, observation.capture_id)
            if capture is None:
                session.add(
                    BronzeCapture(
                        capture_id=observation.capture_id,
                        series_namespace=observation.series_namespace,
                        series_id=observation.series_id,
                        manifest_bucket=observation.manifest_bucket,
                        manifest_key=observation.manifest_key,
                        manifest_etag=observation.manifest_etag,
                        manifest_schema_version=observation.manifest_schema_version,
                        first_observed_at=observation.observed_at,
                        last_observed_at=observation.observed_at,
                    )
                )
                return
            location = (capture.manifest_bucket, capture.manifest_key)
            observed_location = (
                observation.manifest_bucket,
                observation.manifest_key,
            )
            if location != observed_location:
                raise CaptureIdentityConflictError(
                    f"capture {observation.capture_id!r} moved from its manifest location"
                )
            capture.series_namespace = observation.series_namespace
            capture.series_id = observation.series_id
            capture.manifest_etag = observation.manifest_etag
            capture.manifest_schema_version = observation.manifest_schema_version
            capture.last_observed_at = observation.observed_at

    def add_hint(self, claim: HintClaim) -> str:
        """Insert a hint once and return the existing ID for an identical claim."""

        identity = (
            EpisodeHint.capture_id == claim.capture_id,
            EpisodeHint.input_data_version == claim.input_data_version,
            EpisodeHint.recipe_version == claim.recipe_version,
            EpisodeHint.method == claim.method,
            EpisodeHint.candidate_namespace == claim.candidate_namespace,
            EpisodeHint.candidate_series_id == claim.candidate_series_id,
            EpisodeHint.candidate_episode == claim.candidate_episode,
        )
        with self._sessions.begin() as session:
            existing = session.scalar(select(EpisodeHint).where(*identity))
            if existing is not None:
                return existing.hint_id
            session.add(
                EpisodeHint(
                    hint_id=claim.hint_id,
                    capture_id=claim.capture_id,
                    candidate_namespace=claim.candidate_namespace,
                    candidate_series_id=claim.candidate_series_id,
                    candidate_episode=claim.candidate_episode,
                    method=claim.method,
                    confidence=claim.confidence,
                    evidence=claim.evidence,
                    input_data_version=claim.input_data_version,
                    recipe_version=claim.recipe_version,
                    dagster_run_id=claim.dagster_run_id,
                )
            )
        return claim.hint_id

    def accept_binding(self, decision: BindingDecision) -> None:
        """Atomically append an accepted decision and move its current head."""

        try:
            with self._sessions.begin() as session:
                locator = (decision.namespace, decision.series_id, decision.episode)
                # A supersession is a compare-and-swap on the current head.
                # Locking the row prevents two workers from both validating
                # against the same old binding and silently choosing whichever
                # transaction commits last.
                current = session.get(
                    CurrentEpisodeBinding,
                    locator,
                    with_for_update=True,
                )
                if current is None and decision.supersedes_binding_id is not None:
                    raise BindingConflictError("cannot supersede a locator without a head")
                if current is not None:
                    if (
                        current.binding_id == decision.binding_id
                        and current.audio_capture_id == decision.audio_capture_id
                    ):
                        return
                    if decision.supersedes_binding_id != current.binding_id:
                        raise BindingConflictError(
                            f"locator already points to binding {current.binding_id!r}"
                        )
                capture_head = session.scalar(
                    select(CurrentEpisodeBinding).where(
                        CurrentEpisodeBinding.audio_capture_id
                        == decision.audio_capture_id
                    ).with_for_update()
                )
                if capture_head is not None and capture_head.binding_id != (
                    decision.supersedes_binding_id
                ):
                    raise BindingConflictError(
                        "capture is already current for another episode locator"
                    )
                binding = EpisodeBinding(
                    binding_id=decision.binding_id,
                    namespace=decision.namespace,
                    series_id=decision.series_id,
                    episode=decision.episode,
                    audio_capture_id=decision.audio_capture_id,
                    decision="accepted",
                    decision_method=decision.decision_method,
                    decision_evidence=decision.decision_evidence,
                    input_data_version=decision.input_data_version,
                    recipe_version=decision.recipe_version,
                    supersedes_binding_id=decision.supersedes_binding_id,
                    dagster_run_id=decision.dagster_run_id,
                )
                session.add(binding)
                # No ORM relationship joins these intentionally small models,
                # so make the FK target durable before inserting/updating the
                # current-head projection in the same transaction.
                session.flush()
                if current is None:
                    session.add(
                        CurrentEpisodeBinding(
                            namespace=decision.namespace,
                            series_id=decision.series_id,
                            episode=decision.episode,
                            binding_id=decision.binding_id,
                            audio_capture_id=decision.audio_capture_id,
                        )
                    )
                else:
                    current.binding_id = decision.binding_id
                    current.audio_capture_id = decision.audio_capture_id
        except IntegrityError as error:
            raise BindingConflictError(
                "binding conflicts with a current locator, capture, or identifier"
            ) from error

    def record_issue(self, claim: ResolutionIssueClaim) -> str:
        """Insert one deterministic quarantine record idempotently."""

        with self._sessions.begin() as session:
            existing = session.get(EpisodeResolutionIssue, claim.issue_id)
            if existing is not None:
                return existing.issue_id
            session.add(
                EpisodeResolutionIssue(
                    issue_id=claim.issue_id,
                    capture_id=claim.capture_id,
                    hint_id=claim.hint_id,
                    kind=claim.kind,
                    details=claim.details,
                    status="open",
                )
            )
        return claim.issue_id

    def get_capture(self, capture_id: str) -> BronzeCapture | None:
        """Return one detached capture header for a partition execution."""

        with self._sessions() as session:
            return session.get(BronzeCapture, capture_id)

    def get_current_binding_for_capture(
        self, capture_id: str
    ) -> EpisodeBinding | None:
        """Return the accepted current binding for one capture, if any."""

        with self._sessions() as session:
            return session.scalar(
                select(EpisodeBinding)
                .join(
                    CurrentEpisodeBinding,
                    CurrentEpisodeBinding.binding_id == EpisodeBinding.binding_id,
                )
                .where(CurrentEpisodeBinding.audio_capture_id == capture_id)
            )

    def get_latest_open_issue(
        self, capture_id: str
    ) -> EpisodeResolutionIssue | None:
        """Return the newest unresolved diagnostic for one capture."""

        with self._sessions() as session:
            return session.scalar(
                select(EpisodeResolutionIssue)
                .where(
                    EpisodeResolutionIssue.capture_id == capture_id,
                    EpisodeResolutionIssue.status == "open",
                )
                .order_by(EpisodeResolutionIssue.created_at.desc())
                .limit(1)
            )

    def resolve_open_issues(self, capture_id: str, *, note: str) -> int:
        """Close stale review items after a current binding is accepted."""

        with self._sessions.begin() as session:
            issues = list(
                session.scalars(
                    select(EpisodeResolutionIssue).where(
                        EpisodeResolutionIssue.capture_id == capture_id,
                        EpisodeResolutionIssue.status == "open",
                    )
                )
            )
            for issue in issues:
                issue.status = "resolved"
                issue.resolution_note = note
            return len(issues)

    def get_current_binding(
        self, namespace: str, series_id: str, episode: str
    ) -> EpisodeBinding | None:
        """Resolve one logical locator through the indexed current-head table."""

        with self._sessions() as session:
            return session.scalar(
                select(EpisodeBinding)
                .join(
                    CurrentEpisodeBinding,
                    CurrentEpisodeBinding.binding_id == EpisodeBinding.binding_id,
                )
                .where(
                    CurrentEpisodeBinding.namespace == namespace,
                    CurrentEpisodeBinding.series_id == series_id,
                    CurrentEpisodeBinding.episode == episode,
                )
            )

    def list_unresolved_capture_ids(self, *, limit: int = 100) -> list[str]:
        """Return capture IDs with no current episode binding."""

        with self._sessions() as session:
            return list(
                session.scalars(
                    select(BronzeCapture.capture_id)
                    .outerjoin(
                        CurrentEpisodeBinding,
                        CurrentEpisodeBinding.audio_capture_id
                        == BronzeCapture.capture_id,
                    )
                    .where(CurrentEpisodeBinding.binding_id.is_(None))
                    .order_by(BronzeCapture.capture_id)
                    .limit(limit)
                )
            )
