from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ja_media_core.kitsunekko import (
    KitsunekkoFileListResponse,
    KitsunekkoSubtitlesClient,
)
from ja_media_core.subtitle_lid import (
    SubtitleLanguage,
    SubtitleLanguageAnalysis,
    SubtitleLanguageIdConfig,
    analyze_subtitle_language,
)
from ja_media_core.subsync import SubtitleCandidate
from ja_media_core.transcripts import SubtitleCue, format_srt, parse_ass, read_subtitle
from ja_media_frontend.subsync.utils import (
    discover_subtitle_file,
    is_supported_remote_subtitle,
    resolve_subtitle_inputs,
    search_remote_subtitles,
    sidecar_path,
)


RemoteSourceKind = Literal["anilist", "tvdb"]


@dataclass(frozen=True)
class SubtitleLookup:
    """Concrete selector for one Kitsunekko series or episode lookup."""

    source: RemoteSourceKind
    external_id: int
    episode_number: int | None = None
    media_kind: str | None = "tv"


@dataclass(frozen=True)
class SubtitleTrack(SubtitleCandidate):
    """A parsed subtitle candidate with cached language-analysis metadata.

    Language analysis is part of candidate preparation rather than either
    presentation layer: both the terminal and browser frontends need the same
    labels and ordering evidence.
    """

    language_analysis: SubtitleLanguageAnalysis | None = None
    language_error: str | None = None
    modified: bool = False
    timing_offset_s: float = 0.0

    @property
    def label(self) -> str:
        """Return a compact label suitable for candidate lists."""

        if self.repo_path:
            return Path(self.repo_path).stem
        return self.path.name

    @property
    def stem_label(self) -> str:
        """Return the visible stem used to detect colliding candidate names."""

        if self.repo_path:
            return Path(self.repo_path).stem
        return self.path.stem

    @property
    def end_s(self) -> float:
        return max((cue.end_s for cue in self.cues), default=0.0)

    @property
    def active_s(self) -> float:
        return sum(max(0.0, cue.end_s - cue.start_s) for cue in self.cues)

    @property
    def timing_offset_label(self) -> str:
        """Return the cumulative manual cue shift in milliseconds."""

        offset_ms = round(self.timing_offset_s * 1000)
        if offset_ms == 0:
            return "0ms"
        return f"{offset_ms:+d}ms"

    @property
    def language_label(self) -> str:
        """Return the compact language bucket used by frontend displays."""

        if self.language_analysis is None:
            return "error" if self.language_error else "?"
        return {
            SubtitleLanguage.JAPANESE: "ja",
            SubtitleLanguage.UNKNOWN: "?",
            SubtitleLanguage.BILINGUAL: "bi",
            SubtitleLanguage.NON_JAPANESE: "non-ja",
            SubtitleLanguage.INSUFFICIENT_TEXT: "short",
        }[self.language_analysis.language]

    @property
    def language_sort_key(self) -> tuple[int, float, float]:
        """Return a best-first key, keeping analysis failures at the end."""

        if self.language_analysis is None:
            return (SubtitleLanguage.INSUFFICIENT_TEXT.rank + 1, 0.0, 0.0)
        return self.language_analysis.sort_key


class SubtitleDestinationExistsError(FileExistsError):
    """Raised when promotion would replace a sidecar without permission."""

    def __init__(self, destination: Path) -> None:
        super().__init__(f"Subtitle sidecar already exists: {destination}")
        self.destination = destination


def load_subtitle_track(
    path: str | Path,
    *,
    language_id_config: SubtitleLanguageIdConfig,
    repo_path: str | None = None,
    subtitle_id: str | None = None,
) -> SubtitleTrack:
    """Parse one local candidate and cache its language analysis."""

    resolved = Path(path).expanduser().resolve()
    cues = read_subtitle(resolved)
    return build_subtitle_track(
        path=resolved,
        cues=cues,
        language_id_config=language_id_config,
        repo_path=repo_path,
        subtitle_id=subtitle_id,
    )


def fetch_episode_files(
    client: KitsunekkoSubtitlesClient,
    lookup: SubtitleLookup,
) -> KitsunekkoFileListResponse:
    """Fetch the remote inventory matching one concrete episode selector."""

    if lookup.episode_number is None:
        raise ValueError("Episode number is required for an episode lookup")
    if lookup.source == "anilist":
        return client.anilist_episode_files(
            lookup.external_id,
            lookup.episode_number,
        )
    return client.tvdb_episode_files(
        lookup.external_id,
        lookup.episode_number,
        media_kind=lookup.media_kind,
    )


def fetch_series_files(
    client: KitsunekkoSubtitlesClient,
    lookup: SubtitleLookup,
) -> KitsunekkoFileListResponse:
    """Fetch the complete remote subtitle inventory for a series."""

    if lookup.source == "anilist":
        return client.anilist_files(lookup.external_id)
    return client.tvdb_files(
        lookup.external_id,
        media_kind=lookup.media_kind,
    )


def materialize_remote_track(
    client: KitsunekkoSubtitlesClient,
    file: Mapping[str, Any],
    *,
    download_dir: str | Path,
    language_id_config: SubtitleLanguageIdConfig,
) -> SubtitleTrack:
    """Download, normalize, parse, and analyze one Kitsunekko candidate.

    ASS is intentionally materialized as normalized SRT because promotion
    always targets a conventional sidecar. SRT bytes are retained verbatim so
    promotion does not unexpectedly rewrite a user's selected release.
    """

    subtitle_id = str(file.get("subtitle_id") or "")
    if not subtitle_id:
        raise ValueError(f"Kitsunekko row is missing subtitle_id: {file}")
    if not is_supported_remote_subtitle(file):
        raise ValueError(f"Unsupported Kitsunekko subtitle row: {file}")

    repo_path = str(file.get("repo_path") or file.get("filename") or subtitle_id)
    filename = Path(
        str(file.get("filename") or Path(repo_path).name or f"{subtitle_id}.srt")
    ).name
    extension = str(file.get("extension") or Path(filename).suffix).lower().lstrip(".")
    destination_dir = Path(download_dir).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    content = client.file_content(subtitle_id)

    if extension == "ass":
        cues = parse_ass(
            content.decode("utf-8-sig", errors="replace"),
        )
        local_path = destination_dir / f"{subtitle_id}-{Path(filename).stem}.srt"
        local_path.write_text(format_srt(cues), encoding="utf-8")
    else:
        local_path = destination_dir / f"{subtitle_id}-{filename}"
        local_path.write_bytes(content)
        cues = read_subtitle(local_path)

    return build_subtitle_track(
        path=local_path,
        cues=cues,
        language_id_config=language_id_config,
        repo_path=repo_path,
        subtitle_id=subtitle_id,
    )


def promote_subtitle(
    candidate: SubtitleCandidate,
    media_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Atomically promote a candidate to the media file's SRT sidecar.

    Existing sidecars require explicit overwrite permission. Selecting the
    sidecar itself is a successful no-op. SRT sources are copied byte-for-byte;
    other supported formats are normalized through the candidate's parsed cues.
    """

    destination = sidecar_path(media_path)
    if _same_file(candidate.path, destination):
        return destination
    if destination.exists() and not overwrite:
        raise SubtitleDestinationExistsError(destination)

    if candidate.path.suffix.lower() == ".srt" and not getattr(candidate, "modified", False):
        _atomic_write_bytes(destination, candidate.path.read_bytes())
    else:
        _atomic_write_bytes(
            destination,
            format_srt(candidate.cues).encode("utf-8"),
        )
    return destination


def build_subtitle_track(
    *,
    path: Path,
    cues: list[SubtitleCue],
    language_id_config: SubtitleLanguageIdConfig,
    repo_path: str | None,
    subtitle_id: str | None,
) -> SubtitleTrack:
    """Build a candidate from already-parsed cues and cache language analysis."""

    try:
        analysis = analyze_subtitle_language(cues, config=language_id_config)
    except Exception as exc:
        return SubtitleTrack(
            path=path,
            cues=cues,
            repo_path=repo_path,
            subtitle_id=subtitle_id,
            language_error=f"{type(exc).__name__}: {exc}",
        )
    return SubtitleTrack(
        path=path,
        cues=cues,
        repo_path=repo_path,
        subtitle_id=subtitle_id,
        language_analysis=analysis,
    )


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return False


def _atomic_write_bytes(destination: Path, content: bytes) -> None:
    """Write bytes beside the destination and atomically replace it.

    This deliberately avoids ``shutil.copy2``: sidecars need content, not source
    mode and timestamp metadata, and some NFS mounts reject metadata updates
    after otherwise successful writes.
    """

    tmp_path: Path | None = None
    try:
        for _ in range(100):
            candidate = destination.with_name(
                f".{destination.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            except FileExistsError:
                continue
            tmp_path = candidate
            break
        else:  # pragma: no cover - UUID collisions are not realistically reachable.
            raise FileExistsError(f"Could not allocate temp file for {destination}")

        with os.fdopen(fd, "wb") as output_file:
            output_file.write(content)
            output_file.flush()
            os.fsync(output_file.fileno())

        os.replace(tmp_path, destination)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
