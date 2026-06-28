from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Protocol

from ja_media_core.http import ServiceHttpClient
from ja_media_core.services import service_base_url

ANILIST_SEARCH_BASE_URL_ENV = "ANILIST_SEARCH_BASE_URL"
ANILIST_SEARCH_GATEWAY_PATH = "/api/v1/anilist"


@dataclass(frozen=True)
class SearchResult:
    """One BM25-ranked anime match."""

    anilist_id: int | None
    title_english: str | None
    title_native: str | None
    title_romaji: str | None
    season: str | None
    season_year: int | str | None
    format: str | None
    score: float

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> SearchResult:
        return cls(
            anilist_id=data.get("anilist_id"),
            title_english=data.get("title_english"),
            title_native=data.get("title_native"),
            title_romaji=data.get("title_romaji"),
            season=data.get("season"),
            season_year=data.get("season_year"),
            format=data.get("format"),
            score=float(data["score"]),
        )


@dataclass(frozen=True)
class SearchResponse:
    """Ordered list of search results."""

    results: tuple[SearchResult, ...]

    @classmethod
    def from_mapping(cls, data: list[dict[str, Any]]) -> SearchResponse:
        return cls(results=tuple(SearchResult.from_mapping(item) for item in data))


@dataclass(frozen=True)
class AnimeMetadata:
    """One AniList metadata row from the local dataset cache."""

    anilist_id: int
    fields: dict[str, Any]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> AnimeMetadata:
        anilist_id = int(data["anilist_id"])
        return cls(
            anilist_id=anilist_id,
            fields={key: value for key, value in data.items() if key != "anilist_id"},
        )

    def get(self, field: str, default: Any = None) -> Any:
        """Return a metadata field by its CSV column name."""
        return self.fields.get(field, default)


class AniListSearchClient(Protocol):
    """Synchronous anime title fuzzy-search client contract."""

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        include_movies: bool = False,
        include_ova: bool = False,
        all_formats: bool = False,
        force_anilist: bool = False,
    ) -> SearchResponse:
        ...

    def anime(
        self, anilist_id: int, *, fields: tuple[str, ...] | None = None
    ) -> AnimeMetadata:
        ...

    def health(self) -> dict[str, Any]:
        ...


class HttpAniListSearchClient:
    """Small HTTPX client for the LAN AniList search service.

    Searches anime by title using BM25 ranking and returns AniList IDs
    for downstream crosswalk resolution.
    """

    def __init__(self, base_url: str | None = None, *, timeout_s: float = 5.0) -> None:
        configured_url = service_base_url(
            base_url,
            (
                os.environ.get(ANILIST_SEARCH_BASE_URL_ENV),
            ),
            ANILIST_SEARCH_GATEWAY_PATH,
        )
        if not configured_url:
            raise ValueError(
                "AniList search base URL is required. Set it via argument, "
                f"{ANILIST_SEARCH_BASE_URL_ENV}, or in your config.toml under [services].root_url"
            )
        self.base_url = self._normalize_base_url(configured_url)
        self.timeout_s = timeout_s
        self._http = ServiceHttpClient(
            self.base_url,
            timeout_s=timeout_s,
            error_label="AniList search request failed",
        )

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        """Accept the former gateway search endpoint as an AniList service root."""
        normalized = base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(normalized)
        if parsed.path.rstrip("/").endswith("/api/v1/anilist/search"):
            return urllib.parse.urlunsplit(
                parsed._replace(path=parsed.path.rstrip("/")[: -len("/search")])
            ).rstrip("/")
        return normalized

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        include_movies: bool = False,
        include_ova: bool = False,
        all_formats: bool = False,
        force_anilist: bool = False,
    ) -> SearchResponse:
        params = urllib.parse.urlencode({
            "query": query,
            "k": str(top_k),
            "include_movies": str(include_movies).lower(),
            "include_ova": str(include_ova).lower(),
            "all_formats": str(all_formats).lower(),
            "force_anilist": str(force_anilist).lower(),
        })
        payload = self._get_json(f"/search?{params}")
        return SearchResponse.from_mapping(payload)

    def anime(
        self, anilist_id: int, *, fields: tuple[str, ...] | None = None
    ) -> AnimeMetadata:
        path = f"/anime/{anilist_id}"
        if fields:
            params = urllib.parse.urlencode({"fields": ",".join(fields)})
            path = f"{path}?{params}"
        payload = self._get_json(path)
        if not isinstance(payload, dict):
            raise RuntimeError("AniList metadata response was not an object")
        return AnimeMetadata.from_mapping(payload)

    def health(self) -> dict[str, Any]:
        return self._get_json("/health")

    def _url(self, path: str) -> str:
        return self._http.url(path)

    def _get_json(self, path: str) -> dict[str, Any] | list[Any]:
        return self._http.get_json(path)
