from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import PTN

from ja_media_core.transcripts import SubtitleCue


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

    parsed = PTN.parse(filename_stem)
    return _first_positive_int(parsed.get("episode"))


def _first_positive_int(value: Any) -> int | None:
    """Recursively extract the first positive integer from a PTN-parse result.

    PTN may return episode data as an int, float, string (with separators like
    ``-``, ``~``, ``_``), or nested collections.  This unwraps the first
    positive integer it encounters.
    """

    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else None
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdecimal():
            parsed = int(cleaned)
            return parsed if parsed > 0 else None
        for separator in ("-", "~", "_", " "):
            if separator in cleaned:
                for part in cleaned.split(separator):
                    result = _first_positive_int(part)
                    if result is not None:
                        return result
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result = _first_positive_int(item)
            if result is not None:
                return result
    return None
