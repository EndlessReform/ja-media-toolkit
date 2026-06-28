from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

ANILIST_MEDIA_FIELDS = """
id
idMal
title { romaji english native userPreferred }
type
format
status
description
startDate { year month day }
endDate { year month day }
season
seasonYear
seasonInt
episodes
duration
chapters
volumes
countryOfOrigin
isLicensed
source
hashtag
trailer { id site thumbnail }
updatedAt
coverImage { extraLarge large medium color }
bannerImage
genres
synonyms
tags { id name description category rank isGeneralSpoiler isMediaSpoiler isAdult }
averageScore
meanScore
popularity
favourites
trending
rankings { id rank type format year season allTime context }
isFavourite
isAdult
isLocked
siteUrl
externalLinks { id url site type language color icon notes isDisabled }
streamingEpisodes { title thumbnail url site }
relations {
  edges {
    id
    relationType
    node { id title { romaji english native } type format status }
  }
}
characters {
  edges {
    id
    role
    name
    voiceActors {
      id
      name { full native }
      languageV2
      image { large medium }
    }
    node {
      id
      name { full native alternative }
      image { large medium }
      description
    }
  }
}
staff {
  edges {
    id
    role
    node {
      id
      name { full native }
      languageV2
      image { large medium }
    }
  }
}
studios {
  edges {
    id
    isMain
    node { id name isAnimationStudio }
  }
}
nextAiringEpisode { id airingAt timeUntilAiring episode mediaId }
airingSchedule { nodes { id airingAt timeUntilAiring episode mediaId } }
recommendations {
  edges {
    node {
      id
      rating
      mediaRecommendation { id title { romaji english native } }
    }
  }
}
reviews { edges { node { id summary rating score } } }
stats {
  scoreDistribution { score amount }
  statusDistribution { status amount }
}
"""

ANILIST_MEDIA_BY_ID_QUERY = f"""
query ($id: Int!) {{
  Media(id: $id, type: ANIME) {{
    {ANILIST_MEDIA_FIELDS}
  }}
}}
"""

ANILIST_MEDIA_SEARCH_QUERY = f"""
query ($search: String!, $page: Int!, $perPage: Int!) {{
  Page(page: $page, perPage: $perPage) {{
    pageInfo {{ total currentPage lastPage hasNextPage perPage }}
    media(type: ANIME, search: $search, sort: SEARCH_MATCH) {{
      {ANILIST_MEDIA_FIELDS}
    }}
  }}
}}
"""


class AsyncLimiterLike(Protocol):
    """Protocol for aiolimiter.AsyncLimiter and small test doubles."""

    async def __aenter__(self) -> Any: ...

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool | None: ...


@dataclass(frozen=True)
class AniListApiError(RuntimeError):
    """Controlled outbound AniList failure with optional Retry-After metadata."""

    message: str
    status_code: int | None = None
    retry_after_seconds: float | None = None

    def __str__(self) -> str:
        return self.message


class AniListGraphQLClient:
    """Async AniList GraphQL transport guarded by a shared rate limiter."""

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: float,
        limiter: AsyncLimiterLike,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.limiter = limiter
        self._http_client = http_client
        self._owns_http_client = http_client is None

    async def execute(
        self,
        query: str,
        variables: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute one GraphQL POST after acquiring the service limiter."""

        async with self.limiter:
            response = await self._client().post(
                self.endpoint,
                json={"query": query, "variables": dict(variables)},
                headers={"Accept": "application/json"},
            )
        self._raise_for_response(response)
        data = response.json()
        if not isinstance(data, dict):
            raise AniListApiError("AniList returned non-object JSON")
        if data.get("errors"):
            raise AniListApiError(f"AniList GraphQL errors: {data['errors']}")
        return data

    async def fetch_media_by_id(self, anilist_id: int) -> dict[str, Any] | None:
        """Fetch one anime by exact AniList ID, returning None for no Media."""

        data = await self.execute(
            ANILIST_MEDIA_BY_ID_QUERY,
            {"id": int(anilist_id)},
        )
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise AniListApiError("AniList response missing data object")
        media = payload.get("Media")
        if media is not None and not isinstance(media, dict):
            raise AniListApiError("AniList Media payload was not an object")
        return media

    async def search_media(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int = 10,
    ) -> list[dict[str, Any]]:
        """Fetch AniList search matches ordered by AniList's SEARCH_MATCH sort."""

        data = await self.execute(
            ANILIST_MEDIA_SEARCH_QUERY,
            {"search": query, "page": int(page), "perPage": int(per_page)},
        )
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise AniListApiError("AniList response missing data object")
        page_payload = payload.get("Page")
        if not isinstance(page_payload, dict):
            raise AniListApiError("AniList response missing Page object")
        media = page_payload.get("media")
        if not isinstance(media, list):
            raise AniListApiError("AniList Page.media payload was not a list")
        invalid = [item for item in media if not isinstance(item, dict)]
        if invalid:
            raise AniListApiError("AniList Page.media contained non-object entries")
        return media

    async def aclose(self) -> None:
        if self._http_client is not None and self._owns_http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=self.timeout_seconds,
                trust_env=False,
                follow_redirects=True,
            )
        return self._http_client

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        if response.status_code == 429:
            raise AniListApiError(
                "AniList rate limit response",
                status_code=429,
                retry_after_seconds=_parse_retry_after(response.headers),
            )
        if response.is_error:
            raise AniListApiError(
                f"AniList HTTP error: {response.status_code} {response.text}",
                status_code=response.status_code,
            )


def _parse_retry_after(headers: httpx.Headers) -> float | None:
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


async def sleep_for_retry_after(error: AniListApiError) -> None:
    """Sleep for Retry-After when AniList supplies one."""

    if error.retry_after_seconds is not None:
        await asyncio.sleep(error.retry_after_seconds)
