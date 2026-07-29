"""Unit tests for bronze identity and commit-marker filtering."""

from io import BytesIO

import pytest

from ja_media_data.settings import DataSettings
from ja_media_data.storage.bronze import BronzeStore, bronze_store_from_settings
from ja_media_data.storage.bronze import _capture_id, _is_manifest_key


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


def test_probe_performs_one_bounded_prefix_listing() -> None:
    class FakeClient:
        def list_objects_v2(self, **kwargs):
            assert kwargs == {
                "Bucket": "media",
                "Prefix": "audio/anime/bronze/",
                "MaxKeys": 1,
            }

    store = object.__new__(BronzeStore)
    store.bucket = "media"
    store.prefix = "audio/anime/bronze/"
    store._client = FakeClient()

    store.probe()


def test_binary_download_is_atomic_and_prefix_scoped(tmp_path) -> None:
    class FakeClient:
        def get_object(self, **kwargs):
            assert kwargs == {
                "Bucket": "media",
                "Key": "audio/anime/bronze/1/show.flac",
            }
            return {"ETag": '"audio-etag"', "Body": BytesIO(b"audio")}

    store = object.__new__(BronzeStore)
    store.bucket = "media"
    store.prefix = "audio/anime/bronze/"
    store._client = FakeClient()
    target = tmp_path / "cache" / "show.flac"

    etag = store.download_file("audio/anime/bronze/1/show.flac", target)

    assert etag == "audio-etag"
    assert target.read_bytes() == b"audio"


def test_bronze_store_uses_typed_settings() -> None:
    settings = DataSettings(
        bronze={
            "endpoint_url": "https://garage.example",
            "bucket": "media-v2",
            "prefix": "captures/v2",
        },
        ducklake={
            "postgres_url": "postgresql://local/test",
            "data_path": "/tmp/ducklake",
        },
        services={"root_url": "http://services"},
    )

    store = bronze_store_from_settings(settings)

    assert store.bucket == "media-v2"
    assert store.prefix == "captures/v2/"


def test_dedicated_bucket_may_use_its_root_as_the_prefix() -> None:
    settings = DataSettings(
        bronze={
            "endpoint_url": "https://garage.example",
            "bucket": "media-v2",
            "prefix": "",
        },
        ducklake={
            "postgres_url": "postgresql://local/test",
            "data_path": "/tmp/ducklake",
        },
        services={"root_url": "http://services"},
    )

    assert bronze_store_from_settings(settings).prefix == ""
