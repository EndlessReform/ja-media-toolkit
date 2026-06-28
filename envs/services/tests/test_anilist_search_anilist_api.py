from __future__ import annotations

from typing import Any

import httpx
import pytest

from ja_media_services.anilist_search.anilist_api import (
    ANILIST_MEDIA_BY_ID_QUERY,
    ANILIST_MEDIA_SEARCH_QUERY,
    AniListApiError,
    AniListGraphQLClient,
)


class CountingLimiter:
    def __init__(self) -> None:
        self.entries = 0
        self.exits = 0

    async def __aenter__(self) -> None:
        self.entries += 1

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.exits += 1


class FakeAsyncClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.posts: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.posts.append({"url": url, **kwargs})
        return self.response


@pytest.mark.asyncio
async def test_fetch_media_by_id_acquires_limiter_once_per_graphql_call() -> None:
    limiter = CountingLimiter()
    http_client = FakeAsyncClient(
        httpx.Response(
            200,
            json={
                "data": {
                    "Media": {
                        "id": 2026,
                        "status": "NOT_YET_RELEASED",
                        "title": {"romaji": "Future Anime"},
                    }
                }
            },
        )
    )
    client = AniListGraphQLClient(
        endpoint="https://example.test/graphql",
        timeout_seconds=15,
        limiter=limiter,
        http_client=http_client,  # type: ignore[arg-type]
    )

    media = await client.fetch_media_by_id(2026)

    assert media == {
        "id": 2026,
        "status": "NOT_YET_RELEASED",
        "title": {"romaji": "Future Anime"},
    }
    assert limiter.entries == 1
    assert limiter.exits == 1
    assert http_client.posts[0]["url"] == "https://example.test/graphql"
    assert http_client.posts[0]["json"]["variables"] == {"id": 2026}
    assert "Media(id: $id, type: ANIME)" in ANILIST_MEDIA_BY_ID_QUERY


@pytest.mark.asyncio
async def test_search_media_acquires_limiter_and_uses_search_match() -> None:
    limiter = CountingLimiter()
    http_client = FakeAsyncClient(
        httpx.Response(
            200,
            json={
                "data": {
                    "Page": {
                        "pageInfo": {"hasNextPage": False},
                        "media": [
                            {
                                "id": 169580,
                                "title": {"romaji": "Class de 2-banme"},
                            }
                        ],
                    }
                }
            },
        )
    )
    client = AniListGraphQLClient(
        endpoint="https://example.test/graphql",
        timeout_seconds=15,
        limiter=limiter,
        http_client=http_client,  # type: ignore[arg-type]
    )

    media = await client.search_media("Class de 2-banme", per_page=5)

    assert media == [{"id": 169580, "title": {"romaji": "Class de 2-banme"}}]
    assert limiter.entries == 1
    assert http_client.posts[0]["json"]["variables"] == {
        "search": "Class de 2-banme",
        "page": 1,
        "perPage": 5,
    }
    assert "sort: SEARCH_MATCH" in ANILIST_MEDIA_SEARCH_QUERY


@pytest.mark.asyncio
async def test_429_exposes_retry_after_without_retrying_in_transport() -> None:
    limiter = CountingLimiter()
    http_client = FakeAsyncClient(
        httpx.Response(
            429,
            text="Too Many Requests",
            headers={"Retry-After": "12"},
        )
    )
    client = AniListGraphQLClient(
        endpoint="https://example.test/graphql",
        timeout_seconds=15,
        limiter=limiter,
        http_client=http_client,  # type: ignore[arg-type]
    )

    with pytest.raises(AniListApiError) as exc_info:
        await client.fetch_media_by_id(2026)

    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after_seconds == 12
    assert len(http_client.posts) == 1
    assert limiter.entries == 1


@pytest.mark.asyncio
async def test_graphql_errors_raise_controlled_api_error() -> None:
    client = AniListGraphQLClient(
        endpoint="https://example.test/graphql",
        timeout_seconds=15,
        limiter=CountingLimiter(),
        http_client=FakeAsyncClient(  # type: ignore[arg-type]
            httpx.Response(200, json={"errors": [{"message": "bad query"}]})
        ),
    )

    with pytest.raises(AniListApiError, match="GraphQL errors"):
        await client.fetch_media_by_id(2026)
