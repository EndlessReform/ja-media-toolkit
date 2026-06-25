from __future__ import annotations

from typing import Any, Protocol
import threading

import duckdb

from ja_media_services.anilist_search.anilist_api import (
    AniListApiError,
    sleep_for_retry_after,
)
from ja_media_services.anilist_search.anilist_flatten import flatten_anime_data
from ja_media_services.anilist_search.fallback_cache import (
    AniListFallbackCache,
    FallbackAnimeRow,
    FallbackTtlPolicy,
)
from ja_media_services.anilist_search.metadata import project_metadata_payload
from ja_media_services.anilist_search.singleflight import ExactIdSingleFlight


class AniListExactClient(Protocol):
    async def fetch_media_by_id(self, anilist_id: int) -> dict[str, Any] | None: ...


class ExactFallbackNotFound(RuntimeError):
    """AniList has no anime for the requested exact ID."""


class ExactFallbackUnavailable(RuntimeError):
    """AniList fallback could not refresh and no stale row was available."""


async def resolve_exact_fallback(
    *,
    anilist_id: int,
    fields: tuple[str, ...] | None,
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    client: AniListExactClient,
    ttl_policy: FallbackTtlPolicy,
    singleflight: ExactIdSingleFlight,
    now: float | None = None,
) -> dict[str, Any]:
    """Resolve an exact ID through the fallback cache and direct AniList.

    Fresh positive and negative cache rows short-circuit immediately. Expired
    positive rows are retained as stale data and returned if the direct refresh
    fails, which protects LAN clients from transient upstream outages.
    """

    fresh = _read_cached(con, db_lock, ttl_policy, anilist_id, fresh_only=True, now=now)
    if fresh is not None:
        if fresh.negative:
            raise ExactFallbackNotFound
        return _project(fresh, fields)

    stale = _read_cached(con, db_lock, ttl_policy, anilist_id, fresh_only=False, now=now)
    if stale is not None and stale.negative:
        stale = None

    try:
        row = await singleflight.run(
            anilist_id,
            lambda: _fetch_and_cache(
                con=con,
                db_lock=db_lock,
                client=client,
                ttl_policy=ttl_policy,
                anilist_id=anilist_id,
                now=now,
            ),
        )
    except AniListApiError as exc:
        _record_error(con, db_lock, ttl_policy, anilist_id, str(exc))
        if stale is not None:
            return _project(stale, fields)
        raise ExactFallbackUnavailable(str(exc)) from exc

    if row.negative:
        raise ExactFallbackNotFound
    return _project(row, fields)


async def _fetch_and_cache(
    *,
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    client: AniListExactClient,
    ttl_policy: FallbackTtlPolicy,
    anilist_id: int,
    now: float | None,
) -> FallbackAnimeRow:
    media = await _fetch_media_honoring_retry_after(client, anilist_id)
    with db_lock:
        cache = AniListFallbackCache(con, ttl_policy=ttl_policy)
        if media is None:
            return cache.upsert_negative_anime(
                anilist_id,
                now=now,
                last_error="AniList returned no Media for exact ID",
            )
        payload = flatten_anime_data(media)
        return cache.upsert_anime(
            anilist_id,
            payload,
            now=now,
            status=_status(payload),
        )


async def _fetch_media_honoring_retry_after(
    client: AniListExactClient,
    anilist_id: int,
) -> dict[str, Any] | None:
    try:
        return await client.fetch_media_by_id(anilist_id)
    except AniListApiError as exc:
        if exc.status_code == 429 and exc.retry_after_seconds is not None:
            await sleep_for_retry_after(exc)
            return await client.fetch_media_by_id(anilist_id)
        raise


def _read_cached(
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    ttl_policy: FallbackTtlPolicy,
    anilist_id: int,
    *,
    fresh_only: bool,
    now: float | None,
) -> FallbackAnimeRow | None:
    with db_lock:
        return AniListFallbackCache(con, ttl_policy=ttl_policy).get_anime(
            anilist_id,
            fresh_only=fresh_only,
            now=now,
        )


def _record_error(
    con: duckdb.DuckDBPyConnection,
    db_lock: threading.Lock,
    ttl_policy: FallbackTtlPolicy,
    anilist_id: int,
    message: str,
) -> None:
    with db_lock:
        AniListFallbackCache(con, ttl_policy=ttl_policy).record_anime_error(
            anilist_id,
            message,
        )


def _project(row: FallbackAnimeRow, fields: tuple[str, ...] | None) -> dict[str, Any]:
    return project_metadata_payload(row.payload, anilist_id=row.aid, fields=fields)


def _status(payload: dict[str, Any]) -> str | None:
    value = payload.get("status")
    return value if isinstance(value, str) else None
