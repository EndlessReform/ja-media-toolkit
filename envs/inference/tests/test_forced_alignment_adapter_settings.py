"""Checks that the adapter reuses the standard Bronze configuration."""

import hashlib
from pathlib import Path
import shutil

from ja_media_inference.forced_alignment.adapter_audio import (
    AdapterSettings,
    AudioCache,
)
from ja_media_inference.forced_alignment.adapter_contracts import AudioCacheRequest


def test_standard_data_config_and_empty_bronze_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config.local.toml"
    config.write_text(
        """[bronze]
endpoint_url = "http://garage:3900"
bucket = "bronze"
prefix = ""
addressing_style = "path"
"""
    )
    monkeypatch.setenv("JA_MEDIA_DATA_CONFIG", str(config))
    monkeypatch.setenv("JA_MEDIA_BRONZE__ACCESS_KEY_ID", "read-only")
    monkeypatch.setenv("JA_MEDIA_BRONZE__SECRET_ACCESS_KEY", "test-secret")

    settings = AdapterSettings.from_environment()

    assert settings.bronze_endpoint_url == "http://garage:3900"
    assert settings.bronze_bucket == "bronze"
    assert settings.bronze_prefix == ""
    assert settings.bronze_access_key_id == "read-only"

    source = tmp_path / "source.ac3"
    source.write_bytes(b"compressed audio")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cache = AudioCache(
        root=tmp_path / "cache",
        allowed_bucket=settings.bronze_bucket,
        allowed_prefix=settings.bronze_prefix,
        store=CopyingStore(source),
    )
    cached, already_cached = cache.ensure(
        AudioCacheRequest(
            object_bucket="bronze",
            object_key="series/episode.ac3",
            sha256=digest,
            codec="ac3",
        )
    )

    assert cached.read_bytes() == source.read_bytes()
    assert already_cached is False


class CopyingStore:
    def __init__(self, source: Path) -> None:
        self.source = source

    def download(self, _object_key: str, destination: Path) -> None:
        shutil.copyfile(self.source, destination)
