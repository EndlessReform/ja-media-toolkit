from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ja_media_core.media_filename import first_positive_episode
from ja_media_core.transcripts import SubtitleCue

ANILIST_DIRECTORY_PATTERN = re.compile(r"^anilist-([1-9]\d*)$", re.IGNORECASE)


@dataclass(frozen=True)
class SubtitleCandidate:
    """One subtitle candidate for alignment or promotion.

    Carries the resolved filesystem path, parsed cues, and optional identifiers
    from the source repository (e.g. Kitsunekko repo path or a numeric ID).
    """

    path: Path
    cues: list[SubtitleCue]
    repo_path: str | None = None
    subtitle_id: str | None = None


def is_supported_subtitle_file(path: str | Path) -> bool:
    """Return ``True`` when the path has a recognized subtitle extension (.srt or .ass)."""

    ext = Path(path).suffix.lower().lstrip(".")
    return ext in {"srt", "ass"}


def infer_episode_number(filename_stem: str) -> int | None:
    """Extract an episode number from a media filename stem using PTN."""

    return first_positive_episode(filename_stem)


def infer_anilist_id(media_path: str | Path) -> int | None:
    """Read an AniList ID from the nearest ``anilist-<id>`` ancestor.

    Derived-audio libraries use the stable AniList identifier as their series
    directory name. Walking from the media file upward keeps the convention
    useful if later layouts add season or disc subdirectories.
    """

    path = Path(media_path).expanduser()
    directories = path.parents if path.suffix else (path, *path.parents)
    for directory in directories:
        match = ANILIST_DIRECTORY_PATTERN.fullmatch(directory.name)
        if match:
            return int(match.group(1))
    return None
