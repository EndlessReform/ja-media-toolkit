"""Transaction and lookup tests for the episode-identity ledger."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ja_media_data.database import create_session_factory
from ja_media_data.models import (
    Base,
    BronzeCapture,
    EpisodeBinding,
    EpisodeHint,
    EpisodeResolutionIssue,
)
from ja_media_data.repository import (
    BindingConflictError,
    BindingDecision,
    CaptureIdentityConflictError,
    CaptureObservation,
    HintClaim,
    LedgerRepository,
    ResolutionIssueClaim,
)


@pytest.fixture
def sessions() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return create_session_factory(engine)


@pytest.fixture
def repository(sessions: sessionmaker[Session]) -> LedgerRepository:
    return LedgerRepository(sessions)


def observation(
    capture_id: str, *, minute: int = 0, etag: str = "etag-v1"
) -> CaptureObservation:
    return CaptureObservation(
        capture_id=capture_id,
        series_namespace="anilist",
        series_id="15451",
        manifest_bucket="ja-media-prod",
        manifest_key=f"audio/anime/bronze/15451/metadata/{capture_id}.json",
        manifest_etag=etag,
        manifest_schema_version=2,
        observed_at=datetime(2026, 7, 13, 12, minute, tzinfo=UTC),
    )


def decision(
    binding_id: str,
    capture_id: str,
    episode: str,
    *,
    supersedes: str | None = None,
) -> BindingDecision:
    return BindingDecision(
        binding_id=binding_id,
        namespace="anilist",
        series_id="15451",
        episode=episode,
        audio_capture_id=capture_id,
        decision_method="human-confirmed",
        decision_evidence={"source": "unit-test"},
        input_data_version="etag-v1",
        recipe_version="manual-binding-v1",
        supersedes_binding_id=supersedes,
    )


def test_capture_observation_is_idempotently_refreshed(
    repository: LedgerRepository, sessions: sessionmaker[Session]
) -> None:
    repository.observe_capture(observation("capture-1"))
    repository.observe_capture(observation("capture-1", minute=5, etag="etag-v2"))

    with sessions() as session:
        capture = session.get(BronzeCapture, "capture-1")
        assert capture is not None
        assert capture.manifest_etag == "etag-v2"
        assert capture.first_observed_at == datetime(2026, 7, 13, 12, 0)
        assert capture.last_observed_at - capture.first_observed_at == timedelta(minutes=5)


def test_capture_id_cannot_move_to_another_manifest(
    repository: LedgerRepository,
) -> None:
    repository.observe_capture(observation("capture-1"))
    moved = observation("capture-1")
    moved = CaptureObservation(
        **{**moved.__dict__, "manifest_key": "audio/anime/bronze/other.json"}
    )

    with pytest.raises(CaptureIdentityConflictError):
        repository.observe_capture(moved)


def test_identical_hint_returns_existing_id(
    repository: LedgerRepository, sessions: sessionmaker[Session]
) -> None:
    repository.observe_capture(observation("capture-1"))
    first = HintClaim(
        hint_id="hint-1",
        capture_id="capture-1",
        candidate_namespace="anilist",
        candidate_series_id="15451",
        candidate_episode="3",
        method="filename",
        confidence=0.9,
        evidence={"stem": "episode-03"},
        input_data_version="etag-v1",
        recipe_version="filename-v1",
    )
    duplicate = HintClaim(**{**first.__dict__, "hint_id": "hint-2"})

    assert repository.add_hint(first) == "hint-1"
    assert repository.add_hint(duplicate) == "hint-1"
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(EpisodeHint)) == 1


def test_binding_lookup_and_unresolved_capture_query(
    repository: LedgerRepository,
) -> None:
    repository.observe_capture(observation("capture-1"))
    repository.observe_capture(observation("capture-2"))

    repository.accept_binding(decision("binding-1", "capture-1", "3"))

    current = repository.get_current_binding("anilist", "15451", "3")
    assert current is not None
    assert current.binding_id == "binding-1"
    assert repository.list_unresolved_capture_ids() == ["capture-2"]


def test_conflicting_locator_rolls_back_new_history_row(
    repository: LedgerRepository, sessions: sessionmaker[Session]
) -> None:
    repository.observe_capture(observation("capture-1"))
    repository.observe_capture(observation("capture-2"))
    repository.accept_binding(decision("binding-1", "capture-1", "3"))

    with pytest.raises(BindingConflictError):
        repository.accept_binding(decision("binding-2", "capture-2", "3"))

    with sessions() as session:
        assert session.get(EpisodeBinding, "binding-2") is None


def test_capture_cannot_be_current_for_two_locators(
    repository: LedgerRepository,
) -> None:
    repository.observe_capture(observation("capture-1"))
    repository.accept_binding(decision("binding-1", "capture-1", "3"))

    with pytest.raises(BindingConflictError):
        repository.accept_binding(decision("binding-2", "capture-1", "4"))


def test_supersession_moves_head_and_preserves_history(
    repository: LedgerRepository, sessions: sessionmaker[Session]
) -> None:
    repository.observe_capture(observation("capture-1"))
    repository.observe_capture(observation("capture-2"))
    repository.accept_binding(decision("binding-1", "capture-1", "3"))

    repository.accept_binding(
        decision("binding-2", "capture-2", "3", supersedes="binding-1")
    )

    current = repository.get_current_binding("anilist", "15451", "3")
    assert current is not None
    assert current.binding_id == "binding-2"
    assert repository.list_unresolved_capture_ids() == ["capture-1"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(EpisodeBinding)) == 2


def test_stale_supersession_rolls_back_new_history_row(
    repository: LedgerRepository, sessions: sessionmaker[Session]
) -> None:
    repository.observe_capture(observation("capture-1"))
    repository.observe_capture(observation("capture-2"))
    repository.observe_capture(observation("capture-3"))
    repository.accept_binding(decision("binding-1", "capture-1", "3"))
    repository.accept_binding(
        decision("binding-2", "capture-2", "3", supersedes="binding-1")
    )

    with pytest.raises(BindingConflictError):
        repository.accept_binding(
            decision("binding-3", "capture-3", "3", supersedes="binding-1")
        )

    current = repository.get_current_binding("anilist", "15451", "3")
    assert current is not None
    assert current.binding_id == "binding-2"
    with sessions() as session:
        assert session.get(EpisodeBinding, "binding-3") is None


def test_resolution_issue_is_idempotent_and_inspectable(
    repository: LedgerRepository,
) -> None:
    repository.observe_capture(observation("capture-1"))
    claim = ResolutionIssueClaim(
        issue_id="issue-1",
        capture_id="capture-1",
        hint_id=None,
        kind="ambiguous",
        details={"reason": "no_ordinary_episode"},
    )

    assert repository.record_issue(claim) == "issue-1"
    assert repository.record_issue(claim) == "issue-1"
    issue = repository.get_latest_open_issue("capture-1")
    assert isinstance(issue, EpisodeResolutionIssue)
    assert issue.details["reason"] == "no_ordinary_episode"
