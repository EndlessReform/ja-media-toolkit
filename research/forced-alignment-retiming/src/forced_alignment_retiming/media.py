"""Cache the exact canonical audio and embedded subtitle inputs for one case."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

from ja_media_data.storage.bronze import BronzeStore


_SAFE_SUFFIX = re.compile(r"^[a-z0-9]{1,8}$")
_AC3_BITRATES_KBPS = (
    32, 40, 48, 56, 64, 80, 96, 112, 128, 160,
    192, 224, 256, 320, 384, 448, 512, 576, 640,
)


def cache_canonical_media(
    canonical: dict[str, object], case_root: Path, store: BronzeStore
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Fetch one pinned audio object and its pinned embedded subtitle tracks."""

    audio = _cache_audio(canonical, case_root, store)
    anchors = [
        _cache_subtitle(item, case_root, store)
        for item in canonical["embedded_subtitles"]  # type: ignore[union-attr]
    ]
    return audio, anchors


def _cache_audio(
    canonical: dict[str, object], case_root: Path, store: BronzeStore
) -> dict[str, object]:
    codec = _safe_suffix(canonical.get("audio_codec"), "audio")
    fingerprint = str(canonical["input_fingerprint"])
    destination = case_root / "audio" / f"{fingerprint}.{codec}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        store.download_file(str(canonical["audio_object_key"]), destination)
    return {
        "relative_path": destination.relative_to(case_root).as_posix(),
        "byte_count": destination.stat().st_size,
        "sha256": _file_hash(destination),
        "duration_s": _probe_duration(destination, codec),
    }


def _cache_subtitle(
    locator: dict[str, object], case_root: Path, store: BronzeStore
) -> dict[str, object]:
    body = store.read_text(str(locator["object_key"])).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    suffix = _safe_suffix(locator.get("codec"), "srt")
    destination = case_root / "anchors" / f"{digest}.{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        destination.write_bytes(body)
    return {
        **locator,
        "relative_path": destination.relative_to(case_root).as_posix(),
        "byte_count": len(body),
        "sha256": digest,
    }


def _probe_duration(path: Path, codec: str) -> float:
    if codec != "ac3":
        raise RuntimeError(f"the first slice has no safe duration probe for {codec}")
    with path.open("rb") as stream:
        header = stream.read(5)
    if len(header) != 5 or header[:2] != b"\x0b\x77":
        raise RuntimeError(f"invalid raw AC3 header: {path}")
    frame_size_code = header[4] & 0x3F
    bitrate_index = frame_size_code >> 1
    if bitrate_index >= len(_AC3_BITRATES_KBPS):
        raise RuntimeError(f"invalid AC3 frame size code: {frame_size_code}")
    duration = path.stat().st_size * 8 / (_AC3_BITRATES_KBPS[bitrate_index] * 1000)
    if duration <= 0:
        raise RuntimeError(f"ffprobe reported invalid duration for {path}")
    return duration


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_suffix(value: object, fallback: str) -> str:
    suffix = str(value or fallback).casefold().lstrip(".")
    return suffix if _SAFE_SUFFIX.fullmatch(suffix) else fallback
