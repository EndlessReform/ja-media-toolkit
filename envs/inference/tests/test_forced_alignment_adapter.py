"""CPU-side checks for the compact forced-alignment adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient

from ja_media_inference.forced_alignment.adapter_app import create_app
from ja_media_inference.forced_alignment.adapter_audio import (
    AdapterSettings,
    AudioCache,
)
from ja_media_inference.forced_alignment.text_units import (
    TokenAlignment,
)


def test_adapter_caches_ac3_crops_pcm_and_returns_compact_timings(
    tmp_path: Path,
) -> None:
    source = _make_ac3(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    aligner = InspectingAligner()
    client = _api(tmp_path, source, aligner)
    cache_payload = {
        "object_bucket": "test",
        "object_key": "audio/anime/bronze-v2/show/episode.ac3",
        "sha256": digest,
        "codec": "ac3",
    }

    first = client.post("/audio/cache", json=cache_payload)
    second = client.post("/audio/cache", json=cache_payload)
    aligned = client.post(
        "/align",
        json={
            "audio_id": digest,
            "crop_start_s": 0.5,
            "crop_end_s": 1.75,
            "tokens": [_token_payload()],
        },
    )

    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert aligned.status_code == 200
    assert aligned.json()["alignments"] == [
        {
            "token_id": "token:1",
            "start_s": 0.16,
            "end_s": 0.8,
            "confidence": 0.75,
            "metadata": {"max_probability": 0.75},
        }
    ]
    assert aligner.observed_sample_rate == 16000
    assert aligner.observed_channels == 1
    assert 1.20 <= aligner.observed_duration_s <= 1.30


def test_adapter_rejects_wrong_hash_and_out_of_prefix_key(tmp_path: Path) -> None:
    source = _make_ac3(tmp_path)
    client = _api(tmp_path, source, InspectingAligner())

    wrong_hash = client.post(
        "/audio/cache",
        json={
            "object_bucket": "test",
            "object_key": "audio/anime/bronze-v2/show/episode.ac3",
            "sha256": "0" * 64,
            "codec": "ac3",
        },
    )
    wrong_prefix = client.post(
        "/audio/cache",
        json={
            "object_bucket": "test",
            "object_key": "unrelated/private.ac3",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "codec": "ac3",
        },
    )

    assert wrong_hash.status_code == 422
    assert "SHA-256 mismatch" in wrong_hash.json()["detail"]
    assert wrong_prefix.status_code == 422
    assert "outside" in wrong_prefix.json()["detail"]


def test_adapter_contract_rejects_crops_over_300_seconds(tmp_path: Path) -> None:
    client = _api(tmp_path, _make_ac3(tmp_path), InspectingAligner())

    response = client.post(
        "/align",
        json={
            "audio_id": "a" * 64,
            "crop_start_s": 0,
            "crop_end_s": 301,
            "tokens": [_token_payload()],
        },
    )

    assert response.status_code == 422
    assert "300-second" in response.text


def test_adapter_replaces_a_corrupt_cached_copy(tmp_path: Path) -> None:
    source = _make_ac3(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    client = _api(tmp_path, source, InspectingAligner())
    payload = {
        "object_bucket": "test",
        "object_key": "audio/anime/bronze-v2/show/episode.ac3",
        "sha256": digest,
        "codec": "ac3",
    }
    cache_path = tmp_path / "cache" / f"{digest}.ac3"

    assert client.post("/audio/cache", json=payload).status_code == 200
    cache_path.write_bytes(b"corrupt")
    repaired = client.post("/audio/cache", json=payload)

    assert repaired.status_code == 200
    assert repaired.json()["cached"] is False
    assert hashlib.sha256(cache_path.read_bytes()).hexdigest() == digest


def test_health_reports_when_vllm_is_unavailable(tmp_path: Path) -> None:
    client = _api(
        tmp_path,
        _make_ac3(tmp_path),
        InspectingAligner(),
        upstream_ready=lambda: False,
    )

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["status"] == "vllm-unavailable"


class CopyingStore:
    def __init__(self, source: Path) -> None:
        self.source = source

    def download(self, _object_key: str, destination: Path) -> None:
        shutil.copyfile(self.source, destination)


class InspectingAligner:
    observed_sample_rate = 0
    observed_channels = 0
    observed_duration_s = 0.0

    def align_tokens(self, *, audio_path, tokens):  # type: ignore[no-untyped-def]
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels:format=duration",
                "-of",
                "json",
                str(audio_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        import json

        payload = json.loads(probe.stdout)
        stream = payload["streams"][0]
        self.observed_sample_rate = int(stream["sample_rate"])
        self.observed_channels = int(stream["channels"])
        self.observed_duration_s = float(payload["format"]["duration"])
        return [
            TokenAlignment(
                token=tokens[0],
                start_s=0.16,
                end_s=0.8,
                confidence=0.75,
                metadata={"max_probability": 0.75},
            )
        ]


def _api(
    tmp_path: Path,
    source: Path,
    aligner: InspectingAligner,
    *,
    upstream_ready=lambda: True,  # type: ignore[no-untyped-def]
) -> TestClient:
    settings = AdapterSettings(
        vllm_base_url="http://vllm:8000",
        audio_cache_dir=tmp_path / "cache",
        bronze_endpoint_url="http://bronze",
        bronze_bucket="test",
        bronze_prefix="audio/anime/bronze-v2",
        bronze_addressing_style="path",
        bronze_access_key_id=None,
        bronze_secret_access_key=None,
    )
    cache = AudioCache(
        root=settings.audio_cache_dir,
        allowed_bucket=settings.bronze_bucket,
        allowed_prefix=settings.bronze_prefix,
        store=CopyingStore(source),
    )
    return TestClient(
        create_app(
            cache=cache,
            aligner=aligner,
            settings=settings,
            upstream_ready=upstream_ready,
        )
    )


def _make_ac3(tmp_path: Path) -> Path:
    destination = tmp_path / "source.ac3"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-c:a",
            "ac3",
            str(destination),
        ],
        check=True,
    )
    return destination


def _token_payload() -> dict[str, object]:
    return {
        "id": "token:1",
        "text": "台詞",
        "group_id": "cue:1",
        "group_index": 0,
        "char_start": 0,
        "char_end": 2,
    }
