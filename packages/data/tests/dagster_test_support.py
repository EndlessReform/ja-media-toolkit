"""Small domain fixtures for Dagster collection-asset integration tests."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from ja_media_data.products.episode_resolution.metadata import SeriesEpisodeMetadata
from ja_media_data.storage.bronze import BronzeDocument, BronzeMarker


class FakeMetadata:
    """Return stable series facts without making service calls."""

    def get(self, namespace: str, series_id: str) -> SeriesEpisodeMetadata:
        return SeriesEpisodeMetadata(12, "TV", ("Example",))


class FakeStore:
    """Serve manifests and subtitles while allowing downstream failure injection."""

    bucket = "bronze"

    def __init__(self, documents: list[BronzeDocument]) -> None:
        self.documents = documents
        self.last_limit: int | None = None
        self.fail_manifests = False
        self.fail_text = False
        self.manifests = {item.marker.key: item.manifest for item in documents}

    def scan_documents(self, *, limit: int | None):
        self.last_limit = limit
        yield from self.documents if limit is None else self.documents[:limit]

    def read_manifest(self, key: str, *, expected_etag: str | None = None):
        if self.fail_manifests:
            raise RuntimeError("simulated manifest read failure")
        return self.manifests[key]

    def read_text(self, key: str) -> str:
        if self.fail_text:
            raise RuntimeError("simulated subtitle read failure")
        return "1\n00:00:00,000 --> 00:00:01,000\nこれは日本語です。\n"


class FakeOverrides:
    """Expose a mutable revision and optional selected capture."""

    def __init__(self) -> None:
        self.revision = 0
        self.capture_id: str | None = None

    def current_revision(self) -> int:
        return self.revision

    def iter_current_overrides(self):
        if self.revision:
            yield SimpleNamespace(
                override_id=f"override-{self.revision}",
                namespace="anilist",
                series_id="15451",
                episode="3",
                audio_capture_id=self.capture_id,
            )


def documents() -> list[BronzeDocument]:
    """Return two competing valid captures and one legitimate quarantine."""

    return [
        _document("capture-old", "2026-01-01T00:00:00Z", "etag-old", "old"),
        _document("capture-new", "2026-02-01T00:00:00Z", "etag-new", "new"),
        BronzeDocument(
            marker=BronzeMarker(
                "capture-invalid",
                "audio/anime/bronze/999/metadata/invalid.json",
                "etag-invalid",
                10,
                "2026-03-01T00:00:00Z",
            ),
            manifest={"not": "a valid manifest"},
        ),
    ]


def changed_documents(source: list[BronzeDocument]) -> list[BronzeDocument]:
    """Advance one marker version while retaining the same capture identity."""

    changed = list(source)
    first = changed[0]
    changed[0] = replace(
        first,
        marker=replace(first.marker, etag="etag-old-v2"),
    )
    return changed


def _document(
    capture_id: str, modified: str, etag: str, stem: str
) -> BronzeDocument:
    key = f"audio/anime/bronze/15451/metadata/{stem}.json"
    return BronzeDocument(
        marker=BronzeMarker(capture_id, key, etag, 100, modified),
        manifest={
            "schema_version": 2,
            "capture_id": capture_id,
            "series": {"namespace": "anilist", "id": "15451"},
            "source": f"/downloads/Example_Ep03_{stem}.mkv",
            "stem": "Example_Ep03",
            "audio": {
                "filename": f"{stem}.ac3",
                "stream_index": 1,
                "codec": "ac3",
                "declared_language": "jpn",
                "is_default": True,
            },
            "subtitles": [
                {
                    "filename": f"{stem}.srt",
                    "object_key": f"audio/anime/bronze/15451/subs/{stem}.srt",
                    "stream_index": 2,
                    "codec": "srt",
                    "declared_language": "jpn",
                }
            ],
        },
    )
