from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal, Protocol


AnimeIdSource = Literal[
    "anidb",
    "mal",
    "anilist",
    "kitsu",
    "tvdb",
    "tmdb",
    "imdb",
    "anime-planet",
    "anisearch",
    "animenewsnetwork",
    "livechart",
    "simkl",
]
MediaKind = Literal["tv", "series", "movie"]

ANIME_CROSSWALK_URL_ENV = "JA_MEDIA_ANIME_CROSSWALK_URL"


@dataclass(frozen=True)
class CrosswalkLookupRequest:
    """A typed request for one anime metadata ID lookup.

    ``external_id`` is deliberately string-like even when an upstream system uses
    integers. Some sources use mixed identifier formats, and preserving that
    boundary avoids lossy caller-side normalization.
    """

    source: str
    external_id: str
    media_kind: str | None = None


@dataclass(frozen=True)
class CrosswalkLookupResponse:
    """Lookup response shared by the service and reusable clients."""

    source: str
    external_id: str
    media_kind: str | None
    count: int
    results: tuple[dict[str, Any], ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> CrosswalkLookupResponse:
        """Parse the JSON API response into the stable core DTO."""

        results = data.get("results", ())
        return cls(
            source=str(data["source"]),
            external_id=str(data["id"]),
            media_kind=data.get("media_kind"),
            count=int(data.get("count", len(results))),
            results=tuple(dict(result) for result in results),
        )


@dataclass(frozen=True)
class CrosswalkStats:
    """Observable service/source metadata returned by ``/stats``."""

    values: dict[str, str]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> CrosswalkStats:
        return cls(values={str(key): str(value) for key, value in data.items()})


class AnimeCrosswalkClient(Protocol):
    """Synchronous anime metadata crosswalk client contract."""

    def resolve(
        self,
        source: str,
        external_id: str | int,
        media_kind: str | None = None,
    ) -> CrosswalkLookupResponse:
        ...

    def stats(self) -> CrosswalkStats:
        ...

    def health(self) -> dict[str, Any]:
        ...


def normalize_source(source: str) -> str:
    """Normalize user-facing aliases to the service's source names."""

    normalized = source.strip().lower().replace("_", "-")
    aliases = {
        "thetvdb": "tvdb",
        "themoviedb": "tmdb",
        "movie-db": "tmdb",
        "myanimelist": "mal",
        "ani-db": "anidb",
        "ani-db-id": "anidb",
        "anidb-id": "anidb",
    }
    return aliases.get(normalized, normalized)


def normalize_media_kind(media_kind: str | None) -> str | None:
    """Normalize media-kind aliases while preserving broad lookups as ``None``."""

    if media_kind is None:
        return None
    normalized = media_kind.strip().lower().replace("_", "-")
    aliases = {"series": "tv", "show": "tv", "television": "tv", "film": "movie"}
    return aliases.get(normalized, normalized)


def resolve_path(source: str, external_id: str | int, media_kind: str | None = None) -> str:
    """Build the canonical path for a lookup endpoint."""

    normalized_source = normalize_source(source)
    normalized_kind = normalize_media_kind(media_kind)
    encoded_source = urllib.parse.quote(normalized_source, safe="")
    encoded_id = urllib.parse.quote(str(external_id), safe="")
    if normalized_kind is None:
        return f"/resolve/{encoded_source}/{encoded_id}"
    encoded_kind = urllib.parse.quote(normalized_kind, safe="")
    return f"/resolve/{encoded_source}/{encoded_kind}/{encoded_id}"


class HttpAnimeCrosswalkClient:
    """Small standard-library HTTP client for the LAN anime crosswalk service.

    Core avoids service-only dependencies so lightweight tools can use the
    contract without pulling in FastAPI or httpx. The client keeps ambiguity
    visible: if the service returns multiple matching rows, callers receive all
    of them.
    """

    def __init__(self, base_url: str | None = None, *, timeout_s: float = 5.0) -> None:
        configured_url = base_url or os.environ.get(ANIME_CROSSWALK_URL_ENV)
        if not configured_url:
            raise ValueError(
                "Anime crosswalk base URL is required, or set "
                f"{ANIME_CROSSWALK_URL_ENV}"
            )
        self.base_url = configured_url.rstrip("/")
        self.timeout_s = timeout_s

    def resolve(
        self,
        source: str,
        external_id: str | int,
        media_kind: str | None = None,
    ) -> CrosswalkLookupResponse:
        payload = self._get_json(resolve_path(source, external_id, media_kind))
        return CrosswalkLookupResponse.from_mapping(payload)

    def tvdb(self, external_id: str | int) -> CrosswalkLookupResponse:
        return self.resolve("tvdb", external_id)

    def tvdb_movie(self, external_id: str | int) -> CrosswalkLookupResponse:
        return self.resolve("tvdb", external_id, media_kind="movie")

    def tvdb_series(self, external_id: str | int) -> CrosswalkLookupResponse:
        return self.resolve("tvdb", external_id, media_kind="tv")

    def mal(self, external_id: str | int) -> CrosswalkLookupResponse:
        return self.resolve("mal", external_id)

    def anidb(self, external_id: str | int) -> CrosswalkLookupResponse:
        return self.resolve("anidb", external_id)

    def tmdb_tv(self, external_id: str | int) -> CrosswalkLookupResponse:
        return self.resolve("tmdb", external_id, media_kind="tv")

    def tmdb_movie(self, external_id: str | int) -> CrosswalkLookupResponse:
        return self.resolve("tmdb", external_id, media_kind="movie")

    def stats(self) -> CrosswalkStats:
        return CrosswalkStats.from_mapping(self._get_json("/stats"))

    def health(self) -> dict[str, Any]:
        return self._get_json("/healthz")

    def _get_json(self, path: str) -> dict[str, Any]:
        url = urllib.parse.urljoin(f"{self.base_url}/", path.lstrip("/"))
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anime crosswalk request failed: {error.code} {body}") from error
