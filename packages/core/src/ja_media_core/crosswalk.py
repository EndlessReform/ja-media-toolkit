from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol

from ja_media_core.services import service_base_url


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

ANIME_CROSSWALK_BASE_URL_ENV = "ANIME_CROSSWALK_BASE_URL"
ANIME_CROSSWALK_URL_ENV = "JA_MEDIA_ANIME_CROSSWALK_URL"
ANIME_CROSSWALK_GATEWAY_PATH = "/api/v1/crosswalk"

SOURCE_FIELDS = {
    "anidb_id": "anidb",
    "mal_id": "mal",
    "anilist_id": "anilist",
    "kitsu_id": "kitsu",
    "tvdb_id": "tvdb",
    "imdb_id": "imdb",
    "anime-planet_id": "anime-planet",
    "anisearch_id": "anisearch",
    "animenewsnetwork_id": "animenewsnetwork",
    "livechart_id": "livechart",
    "simkl_id": "simkl",
}


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
class CrosswalkBulkLookupResponse:
    """Ordered response for several independent crosswalk lookups."""

    count: int
    results: tuple[CrosswalkLookupResponse, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> CrosswalkBulkLookupResponse:
        """Parse the bulk JSON API response while preserving result order."""

        results = data.get("results", ())
        return cls(
            count=int(data.get("count", len(results))),
            results=tuple(CrosswalkLookupResponse.from_mapping(result) for result in results),
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

    def resolve_many(
        self,
        requests: list[CrosswalkLookupRequest],
    ) -> CrosswalkBulkLookupResponse:
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


def scalar_ids(value: Any) -> Iterable[str]:
    """Yield normalized scalar IDs from an upstream anime-list value.

    Fribb/anime-lists mostly uses integers or strings for external IDs, but
    keeping this tolerant parser in core lets both the crosswalk updater and
    downstream generated indexes handle small source-shape changes the same
    way.
    """

    if value is None or value == "":
        return
    if isinstance(value, list):
        for item in value:
            yield from scalar_ids(item)
        return
    yield str(value)


def infer_tvdb_kind(payload: dict[str, Any]) -> str:
    """Conservatively infer TVDB media kind from upstream row shape.

    Fribb/anime-lists gives TMDB explicit TV/movie slots but TVDB is less
    direct. Treat movie rows without a TVDB season marker as movies; everything
    else is series-like. Broad kindless TVDB lookup rows are emitted separately,
    so this classification only affects callers that explicitly ask for a kind.
    """

    season = payload.get("season")
    tvdb_season = season.get("tvdb") if isinstance(season, dict) else None
    if payload.get("type") == "MOVIE" and tvdb_season in (None, 0, "0", ""):
        return "movie"
    return "tv"


def anime_list_lookup_rows(
    payload: dict[str, Any],
    row_id: int,
) -> list[tuple[str, str, str | None, int]]:
    """Build lookup-table rows for one upstream anime-list object.

    The tuple shape intentionally matches the generated crosswalk database:
    ``(source, external_id, media_kind, row_id)``. Downstream generated indexes
    can reuse this to materialize compatible lookup joins without reaching
    through service-private code.
    """

    rows: list[tuple[str, str, str | None, int]] = []
    for field, source in SOURCE_FIELDS.items():
        for external_id in scalar_ids(payload.get(field)):
            rows.append((source, external_id, None, row_id))
            if source == "tvdb":
                rows.append((source, external_id, infer_tvdb_kind(payload), row_id))

    tmdb_ids = payload.get("themoviedb_id")
    if isinstance(tmdb_ids, dict):
        for media_kind in ("tv", "movie"):
            for external_id in scalar_ids(tmdb_ids.get(media_kind)):
                rows.append(("tmdb", external_id, media_kind, row_id))
                rows.append(("tmdb", external_id, None, row_id))
    else:
        for external_id in scalar_ids(tmdb_ids):
            rows.append(("tmdb", external_id, None, row_id))
    return rows


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
        configured_url = service_base_url(
            base_url,
            (
                os.environ.get(ANIME_CROSSWALK_BASE_URL_ENV),
                os.environ.get(ANIME_CROSSWALK_URL_ENV),
            ),
            ANIME_CROSSWALK_GATEWAY_PATH,
        )
        if not configured_url:
            raise ValueError(
                "Anime crosswalk base URL is required. Set it via argument, "
                f"{ANIME_CROSSWALK_BASE_URL_ENV}, or in your config.toml under [services].root_url"
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

    def resolve_many(
        self,
        requests: list[CrosswalkLookupRequest],
    ) -> CrosswalkBulkLookupResponse:
        """Resolve several IDs in one HTTP request.

        The service returns one normal lookup payload per input item. No-match
        items stay successful ``count: 0`` responses, just like single lookups.
        """

        payload = self._post_json(
            "/resolve/bulk",
            {
                "lookups": [
                    {
                        "source": request.source,
                        "id": request.external_id,
                        "media_kind": request.media_kind,
                    }
                    for request in requests
                ]
            },
        )
        return CrosswalkBulkLookupResponse.from_mapping(payload)

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

    def _url(self, path: str) -> str:
        return urllib.parse.urljoin(f"{self.base_url}/", path.lstrip("/"))

    def _get_json(self, path: str) -> dict[str, Any]:
        url = self._url(path)
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anime crosswalk request failed: {error.code} {body}") from error

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._url(path)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anime crosswalk request failed: {error.code} {body}") from error
