"""End-to-end resolver service tests over the transactional ledger."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ja_media_data.bronze_store import BronzeDocument, BronzeMarker
from ja_media_data.database import create_session_factory
from ja_media_data.episode_metadata import SeriesEpisodeMetadata
from ja_media_data.models import Base
from ja_media_data.repository import LedgerRepository
from ja_media_data.resolution_service import resolve_document


class FakeStore:
    bucket = "integration-test"


class FakeMetadata:
    def get(self, namespace: str, series_id: str) -> SeriesEpisodeMetadata:
        assert namespace == "anilist"
        assert series_id == "15451"
        return SeriesEpisodeMetadata(
            episode_count=12,
            media_format="TV",
            titles=("Example",),
        )


@pytest.fixture
def repository() -> LedgerRepository:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = create_session_factory(engine)
    return LedgerRepository(sessions)


def document(
    capture_id: str, *, stem: str = "Example_Ep03", etag: str = "etag-v1"
) -> BronzeDocument:
    return BronzeDocument(
        marker=BronzeMarker(
            capture_id=capture_id,
            key=f"audio/anime/bronze/15451/metadata/{capture_id}.json",
            etag=etag,
            size=100,
            last_modified="2026-07-13T12:00:00Z",
        ),
        manifest={
            "source": f"/downloads/{stem}.mkv",
            "stem": stem,
            "audio": {
                "filename": f"{stem}.ac3",
                "stream_index": 1,
                "codec": "ac3",
                "declared_language": "jpn",
                "is_default": True,
            },
            "subtitles": [],
        },
    )


def test_apply_is_idempotent_and_overlap_becomes_issue(
    repository: LedgerRepository,
) -> None:
    first = resolve_document(
        document("capture-1"),
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )
    repeated = resolve_document(
        document("capture-1"),
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )
    overlap = resolve_document(
        document("capture-2"),
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )

    assert first.classification == "accepted"
    assert repeated.classification == "accepted"
    assert overlap.classification == "quarantined"
    assert overlap.issue_kind == "overlap"
    issue = repository.get_latest_open_issue("capture-2")
    assert issue is not None
    assert issue.kind == "overlap"


def test_invalid_manifest_is_written_to_review_queue(
    repository: LedgerRepository,
) -> None:
    invalid = document("capture-bad")
    invalid = BronzeDocument(marker=invalid.marker, manifest={"not": "a manifest"})

    result = resolve_document(
        invalid,
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )

    assert result.reason == "invalid_manifest"
    issue = repository.get_latest_open_issue("capture-bad")
    assert issue is not None
    assert issue.kind == "invalid"


def test_new_input_version_safely_supersedes_same_locator(
    repository: LedgerRepository,
) -> None:
    resolve_document(
        document("capture-1", etag="etag-v1"),
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )
    first = repository.get_current_binding_for_capture("capture-1")
    assert first is not None

    result = resolve_document(
        document("capture-1", etag="etag-v2"),
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )

    current = repository.get_current_binding_for_capture("capture-1")
    assert result.classification == "accepted"
    assert current is not None
    assert current.binding_id != first.binding_id
    assert current.supersedes_binding_id == first.binding_id
