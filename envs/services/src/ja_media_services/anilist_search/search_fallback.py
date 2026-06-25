from __future__ import annotations

from typing import Any, Protocol
import threading

import duckdb

from ja_media_services.anilist_search.anilist_api import (
    AniListApiError,
    sleep_for_retry_after,
)
from ja_media_services.anilist_search.anilist_flatten import flatten_anime_data
from ja_media_services.anilist_search.db import resolve_formats
from ja_media_services.anilist_search.fallback_cache import (
    AniListFallbackCache,
    FallbackAnimeRow,
    FallbackTtlPolicy,
)
from ja_media_services.anilist_search.fallback_query_cache import (
    AniListFallbackQueryCache,
    FallbackQueryRow,
    query_cache_key,
)
from ja_media_services.anilist_search.observability import FallbackObserver


class AniListSearchFallbackClient(Protocol):
    async def search_media(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int = 10,
    ) -> list[dict[str, Any]]: ...


class SearchFallbackUnavailable(RuntimeError):
    """AniList direct search failed and no usable stale cache was available."""


async def resolve_search_fallback(
    *,
    query: str,
    top_k: int,
    include_movies: bool,
    include_ova: bool,
    all_formats: bool,
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    client: AniListSearchFallbackClient,
    ttl_policy: FallbackTtlPolicy,
    observer: FallbackObserver | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Resolve a forced title search through AniList and durable DuckDB caches."""

    _increment(observer, "search_requests")
    cache_key = query_cache_key(
        query=query,
        top_k=top_k,
        include_movies=include_movies,
        include_ova=include_ova,
        all_formats=all_formats,
    )
    fresh = _read_query_cache(con, db_lock, cache_key, fresh_only=True, now=now)
    if fresh is not None:
        _increment(observer, "search_cache_hits")
        return _rows_for_cached_ids(con, db_lock, ttl_policy, fresh.result_ids)
    _increment(observer, "search_cache_misses")

    stale = _read_query_cache(con, db_lock, cache_key, fresh_only=False, now=now)
    try:
        return await _fetch_cache_and_project(
            query=query,
            top_k=top_k,
            include_movies=include_movies,
            include_ova=include_ova,
            all_formats=all_formats,
            con=con,
            db_lock=db_lock,
            client=client,
            ttl_policy=ttl_policy,
            observer=observer,
            now=now,
        )
    except AniListApiError as exc:
        _record_query_error(con, db_lock, cache_key, str(exc))
        if stale is not None:
            return _rows_for_cached_ids(con, db_lock, ttl_policy, stale.result_ids)
        raise SearchFallbackUnavailable(str(exc)) from exc


async def _fetch_cache_and_project(
    *,
    query: str,
    top_k: int,
    include_movies: bool,
    include_ova: bool,
    all_formats: bool,
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    client: AniListSearchFallbackClient,
    ttl_policy: FallbackTtlPolicy,
    observer: FallbackObserver | None,
    now: float | None,
) -> list[dict[str, Any]]:
    per_page = max(min(top_k * 3, 50), min(top_k, 50), 10)
    media = await _search_honoring_retry_after(
        client,
        query,
        per_page=per_page,
        observer=observer,
    )
    formats = resolve_formats(include_movies, include_ova, all_formats)
    payloads = []
    for item in media:
        payload = flatten_anime_data(item)
        if _matches_format(payload, formats):
            payloads.append(payload)
        if len(payloads) >= top_k:
            break

    with db_lock:
        anime_cache = AniListFallbackCache(con, ttl_policy=ttl_policy)
        rows = [
            anime_cache.upsert_anime(
                int(payload["id"]),
                payload,
                now=now,
                status=_status(payload),
            )
            for payload in payloads
            if payload.get("id") is not None
        ]
        AniListFallbackQueryCache(con).upsert(
            query=query,
            top_k=top_k,
            include_movies=include_movies,
            include_ova=include_ova,
            all_formats=all_formats,
            result_ids=[int(row.aid) for row in rows],
            now=now,
            ttl_seconds=ttl_policy.airing_seconds,
        )
    return [_search_result(row, rank=index + 1) for index, row in enumerate(rows)]


async def _search_honoring_retry_after(
    client: AniListSearchFallbackClient,
    query: str,
    *,
    per_page: int,
    observer: FallbackObserver | None,
) -> list[dict[str, Any]]:
    try:
        _increment(observer, "outbound_requests")
        return await client.search_media(query, page=1, per_page=per_page)
    except AniListApiError as exc:
        if exc.status_code == 429:
            _increment(observer, "outbound_429s")
        if exc.status_code == 429 and exc.retry_after_seconds is not None:
            await sleep_for_retry_after(exc)
            try:
                _increment(observer, "outbound_requests")
                return await client.search_media(query, page=1, per_page=per_page)
            except AniListApiError:
                _increment(observer, "outbound_errors")
                raise
        _increment(observer, "outbound_errors")
        raise


def _read_query_cache(
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    cache_key: str,
    *,
    fresh_only: bool,
    now: float | None,
) -> FallbackQueryRow | None:
    with db_lock:
        return AniListFallbackQueryCache(con).get(
            cache_key,
            fresh_only=fresh_only,
            now=now,
        )


def _rows_for_cached_ids(
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    ttl_policy: FallbackTtlPolicy,
    result_ids: list[int],
) -> list[dict[str, Any]]:
    with db_lock:
        anime_cache = AniListFallbackCache(con, ttl_policy=ttl_policy)
        rows = [
            row
            for result_id in result_ids
            if (row := anime_cache.get_anime(result_id, fresh_only=False)) is not None
            and not row.negative
        ]
    return [_search_result(row, rank=index + 1) for index, row in enumerate(rows)]


def _record_query_error(
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    cache_key: str,
    message: str,
) -> None:
    with db_lock:
        AniListFallbackQueryCache(con).record_error(cache_key, message)


def _matches_format(payload: dict[str, Any], formats: tuple[str, ...]) -> bool:
    return payload.get("format") in formats


def _search_result(row: FallbackAnimeRow, *, rank: int) -> dict[str, Any]:
    payload = row.payload
    return {
        "anilist_id": int(row.aid),
        "title_english": payload.get("title_english"),
        "title_native": payload.get("title_native"),
        "title_romaji": payload.get("title_romaji"),
        "season": payload.get("season"),
        "season_year": _safe_int(payload.get("seasonYear")),
        "format": payload.get("format"),
        "score": round(1 / rank, 4),
    }


def _status(payload: dict[str, Any]) -> str | None:
    value = payload.get("status")
    return value if isinstance(value, str) else None


def _safe_int(value: Any) -> int | str | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _increment(observer: FallbackObserver | None, name: str) -> None:
    if observer is not None:
        observer.increment(name)
