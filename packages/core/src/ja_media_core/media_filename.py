"""Shared media-filename parsing primitives.

PTN is deliberately treated as a suggestion engine.  Callers that publish
durable episode identity must still ask the user to confirm its output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import PTN


@dataclass(frozen=True)
class ParsedMediaFilename:
    """The small, stable subset of PTN output used by ja-media workflows."""

    title: str | None
    episode_values: tuple[object, ...]


def parse_media_filename(filename_stem: str) -> ParsedMediaFilename:
    """Parse a filename stem without promoting PTN output to authoritative data."""

    parsed = PTN.parse(filename_stem)
    return ParsedMediaFilename(
        title=_clean_text(parsed.get("title")),
        episode_values=_flatten_values(parsed.get("episode")),
    )


def suggest_ordinary_episode(filename_stem: str) -> int | None:
    """Return one unambiguous positive integer episode suggestion.

    Multi-episode, fractional, special, and otherwise ambiguous values return
    ``None`` so an interactive caller can request an explicit mapping.
    """

    values = parse_media_filename(filename_stem).episode_values
    if len(values) != 1:
        return None
    candidate = _positive_int(values[0])
    if candidate is None or _looks_ambiguous(filename_stem, candidate):
        return None
    return candidate


def first_positive_episode(filename_stem: str) -> int | None:
    """Preserve the legacy first-positive-integer behavior for older callers."""

    for value in parse_media_filename(filename_stem).episode_values:
        result = _positive_int(value)
        if result is not None:
            return result
    return None


def _flatten_values(value: Any) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(item for value_item in value for item in _flatten_values(value_item))
    if isinstance(value, str):
        cleaned = value.strip()
        for separator in ("-", "~", "_", " "):
            if separator in cleaned:
                return tuple(part for part in cleaned.split(separator) if part)
        return (cleaned,) if cleaned else ()
    return (value,)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else None
    if isinstance(value, str) and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _looks_ambiguous(filename_stem: str, candidate: int) -> bool:
    """Catch ranges/fractions that PTN sometimes collapses to one endpoint."""

    for match in re.finditer(r"(?<!\d)(\d+)\s*[-~_]\s*(\d+)(?!\d)", filename_stem):
        if candidate in (int(match.group(1)), int(match.group(2))):
            return True
    for match in re.finditer(r"(?<!\d)(\d+)\.(\d+)(?!\d)", filename_stem):
        if candidate in (int(match.group(1)), int(match.group(2))):
            return True
    return False


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
