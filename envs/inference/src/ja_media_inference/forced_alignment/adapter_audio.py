"""Read-only Bronze caching and exact crop decoding for the Qwen adapter."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Iterator, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from ja_media_inference.forced_alignment.adapter_contracts import AudioCacheRequest


class AudioObjectStore(Protocol):
    """Small read-only object operation needed by the adapter."""

    def download(self, object_key: str, destination: Path) -> None: ...


@dataclass(frozen=True)
class AdapterSettings:
    """Process settings for the deliberately narrow colocated adapter."""

    vllm_base_url: str
    audio_cache_dir: Path
    bronze_endpoint_url: str
    bronze_bucket: str
    bronze_prefix: str
    bronze_addressing_style: str
    bronze_access_key_id: str | None
    bronze_secret_access_key: str | None

    @classmethod
    def from_environment(cls) -> AdapterSettings:
        """Load settings without reading or printing a dotenv file."""

        required = {
            "ALIGNER_BRONZE_ENDPOINT_URL": os.environ.get(
                "ALIGNER_BRONZE_ENDPOINT_URL"
            ),
            "ALIGNER_BRONZE_BUCKET": os.environ.get("ALIGNER_BRONZE_BUCKET"),
            "ALIGNER_BRONZE_PREFIX": os.environ.get("ALIGNER_BRONZE_PREFIX"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"missing adapter settings: {', '.join(missing)}")
        return cls(
            vllm_base_url=os.environ.get(
                "ALIGNER_VLLM_BASE_URL", "http://vllm:8000"
            ).rstrip("/"),
            audio_cache_dir=Path(
                os.environ.get("ALIGNER_AUDIO_CACHE_DIR", "/var/cache/ja-media/audio")
            ),
            bronze_endpoint_url=str(required["ALIGNER_BRONZE_ENDPOINT_URL"]),
            bronze_bucket=str(required["ALIGNER_BRONZE_BUCKET"]),
            bronze_prefix=str(required["ALIGNER_BRONZE_PREFIX"]).strip("/"),
            bronze_addressing_style=os.environ.get(
                "ALIGNER_BRONZE_ADDRESSING_STYLE", "path"
            ),
            bronze_access_key_id=_optional_environment("ALIGNER_BRONZE_ACCESS_KEY_ID"),
            bronze_secret_access_key=_optional_environment(
                "ALIGNER_BRONZE_SECRET_ACCESS_KEY"
            ),
        )


class S3AudioObjectStore:
    """Download a pinned audio object through an S3-compatible read client."""

    def __init__(self, settings: AdapterSettings) -> None:
        self._bucket = settings.bronze_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.bronze_endpoint_url,
            aws_access_key_id=settings.bronze_access_key_id,
            aws_secret_access_key=settings.bronze_secret_access_key,
            config=Config(
                s3={"addressing_style": settings.bronze_addressing_style},
                response_checksum_validation="when_required",
            ),
        )

    def download(self, object_key: str, destination: Path) -> None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=object_key)
            with destination.open("wb") as handle:
                body = response["Body"]
                for chunk in iter(lambda: body.read(1024 * 1024), b""):
                    handle.write(chunk)
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"Bronze audio download failed: {object_key}") from exc


class AudioCache:
    """Cache verified compressed episode audio under its content fingerprint."""

    def __init__(
        self,
        *,
        root: Path,
        allowed_bucket: str,
        allowed_prefix: str,
        store: AudioObjectStore,
    ) -> None:
        self.root = root
        self.allowed_bucket = allowed_bucket
        self.allowed_prefix = f"{allowed_prefix.strip('/')}/"
        self.store = store

    def ensure(self, request: AudioCacheRequest) -> tuple[Path, bool]:
        if request.object_bucket != self.allowed_bucket:
            raise ValueError("audio object bucket does not match configured Bronze")
        if not request.object_key.startswith(self.allowed_prefix):
            raise ValueError("audio object key is outside the configured Bronze prefix")
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{request.sha256}.{request.codec}"
        if destination.is_file():
            if _sha256(destination) == request.sha256:
                return destination, True
            destination.unlink()
        with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as handle:
            temporary = Path(handle.name)
        try:
            self.store.download(request.object_key, temporary)
            observed = _sha256(temporary)
            if observed != request.sha256:
                raise ValueError(
                    f"audio SHA-256 mismatch: expected {request.sha256}, got {observed}"
                )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination, False

    def resolve(self, audio_id: str) -> Path:
        matches = list(self.root.glob(f"{audio_id}.*"))
        if len(matches) != 1 or not matches[0].is_file():
            raise FileNotFoundError(f"cached audio not found: {audio_id}")
        return matches[0]


@contextmanager
def decoded_crop(source: Path, *, start_s: float, end_s: float) -> Iterator[Path]:
    """Decode one unpadded source-clock crop to mono 16 kHz PCM."""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        destination = Path(handle.name)
    try:
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    f"{start_s:.3f}",
                    "-i",
                    str(source),
                    "-t",
                    f"{end_s - start_s:.3f}",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(destination),
                ],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"ffmpeg could not decode cached audio: {source}"
            ) from exc
        yield destination
    finally:
        destination.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_environment(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None
