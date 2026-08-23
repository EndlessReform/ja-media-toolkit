"""Cache the exact canonical audio and embedded subtitle inputs for one case."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

from ja_media_data.storage.bronze import BronzeStore


_SAFE_SUFFIX = re.compile(r"^[a-z0-9]{1,8}$")
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
        "object_bucket": canonical["audio_object_bucket"],
        "object_key": canonical["audio_object_key"],
        "stream_index": canonical["audio_stream_index"],
        "codec": canonical.get("audio_codec"),
        "declared_language": canonical.get("audio_declared_language"),
        "input_fingerprint": fingerprint,
        "relative_path": destination.relative_to(case_root).as_posix(),
        "byte_count": destination.stat().st_size,
        "sha256": _file_hash(destination),
        "duration_s": _probe_duration(destination),
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


def _probe_duration(path: Path) -> float:
    """Read duration through ffprobe so the cache accepts any canonical codec."""

    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(completed.stdout.strip())
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
