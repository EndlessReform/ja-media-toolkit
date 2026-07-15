"""AniList metadata adapter used as an independent episode sanity check."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from ja_media_core.anilist_search import HttpAniListSearchClient


@dataclass(frozen=True)
class SeriesEpisodeMetadata:
    """The bounded AniList facts relevant to episode acceptance policy."""

    episode_count: int | None
    media_format: str | None
    titles: tuple[str, ...]


class EpisodeMetadataProvider(Protocol):
    """Look up independent series facts without coupling policy to HTTP."""

    def get(self, namespace: str, series_id: str) -> SeriesEpisodeMetadata | None:
        ...


class AniListEpisodeMetadataProvider:
    """Cache exact-ID metadata reads for one local resolver process."""

    def __init__(self, client: HttpAniListSearchClient | None = None) -> None:
        self._client = client or HttpAniListSearchClient()

    @lru_cache(maxsize=512)
    def get(self, namespace: str, series_id: str) -> SeriesEpisodeMetadata | None:
        if namespace != "anilist" or not series_id.isdecimal():
            return None
        metadata = self._client.anime(
            int(series_id),
            fields=(
                "episodes",
                "format",
                "title_romaji",
                "title_english",
                "title_native",
                "synonyms",
            ),
        )
        episode_count = _positive_int(metadata.get("episodes"))
        titles = tuple(
            dict.fromkeys(
                value
                for value in (
                    _text(metadata.get("title_romaji")),
                    _text(metadata.get("title_english")),
                    _text(metadata.get("title_native")),
                    *_text_list(metadata.get("synonyms")),
                )
                if value is not None
            )
        )
        return SeriesEpisodeMetadata(
            episode_count=episode_count,
            media_format=_text(metadata.get("format")),
            titles=titles,
        )


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdecimal() and int(stripped) > 0:
            return int(stripped)
    return None


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for item in value if (text := _text(item)) is not None)
