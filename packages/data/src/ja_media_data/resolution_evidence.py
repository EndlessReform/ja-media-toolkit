"""Collect parser and metadata signals without applying acceptance policy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
import unicodedata

from ja_media_core.bronze import BronzeCaptureManifest
from ja_media_core.media_filename import (
    parse_media_filename,
    suggest_ordinary_episode,
)

from ja_media_data.episode_metadata import SeriesEpisodeMetadata


_TOKEN_RE = re.compile(
    r"(?ix)(?:s\d{1,2}[ ._-]*e|(?<![a-z0-9])ep(?:isode)?[ ._-]*)(\d{1,4})(?!\d)"
)
_RANGE_RE = re.compile(
    r"(?ix)(?<![a-z0-9])ep(?:isode)?[ ._-]*(\d{1,4})\s*[-~]\s*"
    r"(?:ep(?:isode)?[ ._-]*)?(\d{1,4})(?!\d)"
)


@dataclass(frozen=True)
class EpisodeEvidence:
    """Independent signals used by the versioned resolver policy."""

    ptn_episode: int | None
    explicit_episodes: tuple[int, ...]
    ranges: tuple[tuple[int, int], ...]
    matched_title: str | None
    details: dict[str, Any]


def collect_episode_evidence(
    manifest: BronzeCaptureManifest,
    metadata: SeriesEpisodeMetadata | None,
) -> EpisodeEvidence:
    """Parse a filename once and compare it with exact AniList titles."""

    parsed = parse_media_filename(manifest.stem)
    ptn_episode = suggest_ordinary_episode(manifest.stem, parsed=parsed)
    explicit_episodes = tuple(
        sorted({int(match.group(1)) for match in _TOKEN_RE.finditer(manifest.stem)})
    )
    ranges = tuple(
        (int(match.group(1)), int(match.group(2)))
        for match in _RANGE_RE.finditer(manifest.stem)
    )
    matched_title = _matching_title(parsed.title, metadata)
    return EpisodeEvidence(
        ptn_episode=ptn_episode,
        explicit_episodes=explicit_episodes,
        ranges=ranges,
        matched_title=matched_title,
        details={
            "stem": manifest.stem,
            "source_hint": manifest.source_hint,
            "ptn_title": parsed.title,
            "ptn_episode_values": [str(value) for value in parsed.episode_values],
            "ptn_ordinary_episode": ptn_episode,
            "explicit_episode_tokens": list(explicit_episodes),
            "episode_ranges": [list(value) for value in ranges],
            "series": {
                "namespace": manifest.series.namespace,
                "id": manifest.series.identifier,
            },
            "metadata": _metadata_details(metadata),
            "matched_anilist_title": matched_title,
        },
    )


def _metadata_details(
    metadata: SeriesEpisodeMetadata | None,
) -> dict[str, Any] | None:
    if metadata is None:
        return None
    return {
        "episode_count": metadata.episode_count,
        "format": metadata.media_format,
        "titles": list(metadata.titles),
    }


def _matching_title(
    parsed_title: str | None, metadata: SeriesEpisodeMetadata | None
) -> str | None:
    if parsed_title is None or metadata is None:
        return None
    normalized_parsed = _normalize_title(parsed_title)
    return next(
        (
            title
            for title in metadata.titles
            if _normalize_title(title) == normalized_parsed
        ),
        None,
    )


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())
