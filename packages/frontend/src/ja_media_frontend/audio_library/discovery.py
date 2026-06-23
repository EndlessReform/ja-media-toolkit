"""Immediate-directory media discovery and ffprobe adaptation."""

from __future__ import annotations

import json
import re
import signal
import subprocess
import time
from pathlib import Path

from ja_media_core.audio_library import AudioStreamProbe, SourceMediaProbe
from ja_media_core.media_filename import suggest_ordinary_episode

SUPPORTED_MEDIA_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".webm"})


def discover_media(source_dir: Path) -> tuple[Path, ...]:
    """Return real supported media children in deterministic filename order.

    macOS writes AppleDouble ``._*`` sidecars when copying files to filesystems
    that cannot store resource forks natively. A sidecar retains the original
    filename suffix but is metadata, not playable media, so it must never reach
    ffprobe.
    """

    return tuple(
        sorted(
            (
                path
                for path in source_dir.iterdir()
                if _is_discoverable_media(path)
            ),
            key=lambda path: path.name.casefold(),
        )
    )


def _is_discoverable_media(path: Path) -> bool:
    """Reject filesystem metadata that merely borrows a media suffix."""

    return (
        path.is_file()
        and not path.name.startswith("._")
        and path.suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS
    )


def suggest_episode_key(path: Path) -> str | None:
    """Suggest an ordinary episode key only when PTN is unambiguous."""

    episode = suggest_ordinary_episode(path.stem)
    return str(episode) if episode is not None else None


def identity_search_query(source: Path) -> str:
    """Skip season-only path components when deriving an AniList title query.

    Media managers commonly produce paths such as ``Series/Season 01``. A bare
    season component carries no series identity, so climb past consecutive
    season-only directories while leaving discovery rooted in the source
    directory the user supplied.
    """

    candidate = source
    while _is_bare_season_directory(candidate.name) and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate.name


def probe_media(path: Path) -> SourceMediaProbe:
    """Probe duration and audio streams using ffprobe JSON output."""

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        (
            "format=duration:"
            "stream=index,codec_type,codec_name,channels,sample_rate:"
            "stream_tags=language,title:"
            "stream_disposition=default"
        ),
        "-of",
        "json",
        str(path),
    ]
    result = _run_ffprobe(command, path)
    payload = json.loads(result.stdout)
    audio_streams: list[AudioStreamProbe] = []
    for stream in payload.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        audio_streams.append(
            AudioStreamProbe(
                global_index=int(stream["index"]),
                audio_ordinal=len(audio_streams),
                codec=str(stream.get("codec_name") or "unknown"),
                language=_clean_tag(tags.get("language")),
                title=_clean_tag(tags.get("title")),
                channels=_optional_int(stream.get("channels")),
                sample_rate_hz=_optional_int(stream.get("sample_rate")),
                default=bool(disposition.get("default")),
            )
        )
    stat = path.stat()
    duration = float((payload.get("format") or {}).get("duration") or 0)
    return SourceMediaProbe(
        path=path,
        duration_ms=round(duration * 1000),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        audio_streams=tuple(audio_streams),
    )


def _run_ffprobe(
    command: list[str],
    path: Path,
    *,
    signal_retries: int = 2,
) -> subprocess.CompletedProcess[str]:
    """Run ffprobe, retrying transient process crashes but not normal errors."""

    attempts = signal_retries + 1
    for attempt in range(attempts):
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result
        if result.returncode >= 0:
            detail = result.stderr.strip() or "no diagnostic output"
            raise RuntimeError(f"ffprobe failed for {path}: {detail}")
        signal_number = -result.returncode
        if attempt < attempts - 1:
            time.sleep(0.2 * (attempt + 1))
            continue
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = f"signal {signal_number}"
        raise RuntimeError(
            f"ffprobe crashed with {signal_name} while probing {path} "
            f"({attempts} attempts)"
        )
    raise AssertionError("unreachable")


def choose_unambiguous_audio_stream(
    probe: SourceMediaProbe,
    *,
    preferred_languages: tuple[str, ...] = ("jpn", "ja"),
) -> AudioStreamProbe | None:
    """Choose one stream only when language/default evidence is decisive."""

    language_matches = tuple(
        stream
        for stream in probe.audio_streams
        if (stream.language or "").casefold() in preferred_languages
    )
    if len(language_matches) == 1:
        return language_matches[0]
    if len(language_matches) > 1:
        defaults = tuple(stream for stream in language_matches if stream.default)
        return defaults[0] if len(defaults) == 1 else None
    if len(probe.audio_streams) == 1:
        return probe.audio_streams[0]
    defaults = tuple(stream for stream in probe.audio_streams if stream.default)
    return defaults[0] if len(defaults) == 1 else None


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _clean_tag(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _is_bare_season_directory(name: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?ix)(?:season|series)\s*[-_. ]?\s*\d{1,3}|s\s*[-_. ]?\s*\d{1,3}",
            name.strip(),
        )
    )
