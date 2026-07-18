"""Tests for compiling immutable manifest headers into the bronze cache."""

from datetime import UTC, datetime

from ja_media_data.bronze_store import BronzeDocument, BronzeMarker
from ja_media_data.lakehouse.bronze_hydration import hydrate_bronze_captures
from ja_media_data.resolution_types import ReplaceResult


class FakeStore:
    bucket = "bronze"

    def scan_documents(self, *, limit: int):
        assert limit == 2
        yield _document("capture-good", _manifest())
        yield _document("capture-bad", {"invalid": True})


class RecordingRepository:
    def __init__(self) -> None:
        self.observations = []

    def replace_bronze_captures(
        self, observations, fingerprint, *, scope, run_id
    ) -> ReplaceResult:
        self.observations.extend(observations)
        return ReplaceResult(True, fingerprint, len(observations))


def test_hydration_indexes_valid_and_invalid_commit_markers() -> None:
    repository = RecordingRepository()
    result = hydrate_bronze_captures(
        FakeStore(),
        repository,
        limit=2,
        observed_at=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert (result.observed, result.valid, result.invalid, result.written) == (
        2,
        1,
        1,
        True,
    )
    assert repository.observations[0].series_id == "15451"
    assert repository.observations[0].manifest_modified_at == datetime(
        2026, 7, 13, tzinfo=UTC
    )
    assert repository.observations[1].series_id == "15451"


def _document(capture_id: str, manifest: dict) -> BronzeDocument:
    return BronzeDocument(
        marker=BronzeMarker(
            capture_id=capture_id,
            key=f"audio/anime/bronze/15451/metadata/{capture_id}.json",
            etag="etag",
            size=1,
            last_modified="2026-07-13T00:00:00Z",
        ),
        manifest=manifest,
    )


def _manifest() -> dict:
    return {
        "source": "/downloads/Example_Ep01.mkv",
        "stem": "Example_Ep01",
        "audio": {
            "filename": "Example_Ep01.ac3",
            "stream_index": 1,
            "codec": "ac3",
            "declared_language": "jpn",
            "is_default": True,
        },
        "subtitles": [],
    }
