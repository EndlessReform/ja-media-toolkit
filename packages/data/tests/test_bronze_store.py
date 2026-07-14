"""Unit tests for bronze identity and commit-marker filtering."""

import pytest

from ja_media_data.bronze_store import BronzeStore
from ja_media_data.bronze_store import _capture_id, _is_manifest_key


def test_only_metadata_json_is_a_commit_marker() -> None:
    assert _is_manifest_key("audio/anime/bronze/1/metadata/show.json")
    assert not _is_manifest_key("audio/anime/bronze/1/show.flac")
    assert not _is_manifest_key("audio/anime/bronze/1/subs/show/track.srt")


def test_manifest_capture_id_wins() -> None:
    assert _capture_id({"capture_id": "capture-01J"}, "media", "key") == "capture-01J"


def test_legacy_capture_id_is_deterministic_and_location_scoped() -> None:
    first = _capture_id({}, "media", "metadata/show.json")
    assert first == _capture_id({}, "media", "metadata/show.json")
    assert first != _capture_id({}, "other", "metadata/show.json")
    assert first.startswith("capture-")


def test_manifest_read_rejects_changed_etag() -> None:
    class FakeClient:
        def get_object(self, **kwargs):
            assert kwargs == {
                "Bucket": "media",
                "Key": "audio/anime/bronze/1/metadata/show.json",
            }
            return {"ETag": '"new-etag"', "Body": None}

    store = object.__new__(BronzeStore)
    store.bucket = "media"
    store.prefix = "audio/anime/bronze/"
    store._client = FakeClient()

    with pytest.raises(RuntimeError, match="manifest changed during scan"):
        store.read_manifest(
            "audio/anime/bronze/1/metadata/show.json",
            expected_etag="listed-etag",
        )
