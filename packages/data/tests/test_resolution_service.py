"""End-to-end compiler tests over PostgreSQL-cataloged DuckLake products."""

import os
import uuid

import pytest

from ja_media_data.storage.bronze import BronzeDocument, BronzeMarker
from ja_media_data.products.episode_resolution.metadata import SeriesEpisodeMetadata
from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.products.episode_resolution.compiler import resolve_batch


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
def repository(tmp_path) -> DuckLakeRepository:
    postgres_url = os.environ.get(
        "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
        "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
        "@127.0.0.1:55432/ja_media_lakehouse_test",
    )
    connection = connect_catalog(
        CatalogConfig(
            postgres_url=postgres_url,
            metadata_schema="phase_c2_resolver_" + uuid.uuid4().hex,
            data_path=str(tmp_path / "ducklake"),
        )
    )
    apply_schema(connection)
    yield DuckLakeRepository(connection)
    connection.close()


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


def test_batch_keeps_competing_proposals_and_identical_replay_is_zero_write(
    repository: DuckLakeRepository,
) -> None:
    documents = (document("capture-1"), document("capture-2"))
    first = resolve_batch(
        documents,
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )
    repeated = resolve_batch(
        documents,
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )

    assert [item.classification for item in first.results] == [
        "proposed",
        "proposed",
    ]
    assert first.bronze_write is not None and first.bronze_write.written is True
    assert first.resolution_write is not None
    assert first.resolution_write.written is True
    assert repeated.bronze_write is not None
    assert repeated.bronze_write.written is False
    assert repeated.resolution_write is not None
    assert repeated.resolution_write.written is False
    assert repository.summary()["episode_binding_proposals"] == 2
    assert repository.summary()["resolution_issues_auto"] == 0


def test_manifest_schema_failure_is_in_rebuilt_review_product(
    repository: DuckLakeRepository,
) -> None:
    invalid = document("capture-bad")
    invalid = BronzeDocument(marker=invalid.marker, manifest={"not": "a manifest"})

    batch = resolve_batch(
        (invalid,),
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )

    assert batch.results[0].reason == "bronze_manifest_failed_schema_validation"
    issue = repository.get_latest_open_issue("capture-bad")
    assert issue is not None
    assert issue.kind == "invalid"


def test_changed_input_replaces_proposal_without_supersession(
    repository: DuckLakeRepository,
) -> None:
    first = resolve_batch(
        (document("capture-1", etag="etag-v1"),),
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )
    first_binding_id = repository.connection.execute(
        "SELECT proposal_id FROM episode_binding_proposals"
    ).fetchone()[0]
    changed = resolve_batch(
        (document("capture-1", etag="etag-v2"),),
        store=FakeStore(),
        metadata_provider=FakeMetadata(),
        repository=repository,
    )

    current_binding_id = repository.connection.execute(
        "SELECT proposal_id FROM episode_binding_proposals"
    ).fetchone()[0]
    assert first.resolution_write is not None
    assert changed.resolution_write is not None and changed.resolution_write.written
    assert current_binding_id != first_binding_id
    assert repository.summary()["episode_binding_proposals"] == 1
