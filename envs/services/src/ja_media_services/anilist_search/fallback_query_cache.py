from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time

import duckdb

from ja_media_services.anilist_search.fallback_schema import (
    QUERY_TABLE,
    ensure_fallback_schema,
)


@dataclass(frozen=True)
class FallbackQueryRow:
    """Cached direct AniList search result IDs for a query shape."""

    cache_key: str
    query: str
    top_k: int
    include_movies: bool
    include_ova: bool
    all_formats: bool
    result_ids: list[int]
    fetched_at_unix: float
    expires_at_unix: float
    last_error: str | None

    def is_fresh(self, now: float | None = None) -> bool:
        return self.expires_at_unix > (time.time() if now is None else now)


class AniListFallbackQueryCache:
    """DuckDB-backed direct-search result cache."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con
        ensure_fallback_schema(con)

    def get(
        self,
        cache_key: str,
        *,
        fresh_only: bool = True,
        now: float | None = None,
    ) -> FallbackQueryRow | None:
        row = self.con.execute(
            f"""
            SELECT
                cache_key,
                query,
                top_k,
                include_movies,
                include_ova,
                all_formats,
                result_ids::VARCHAR,
                fetched_at_unix,
                expires_at_unix,
                last_error
            FROM {QUERY_TABLE}
            WHERE cache_key = ?
            """,
            [cache_key],
        ).fetchone()
        if row is None:
            return None
        cached = FallbackQueryRow(
            cache_key=str(row[0]),
            query=str(row[1]),
            top_k=int(row[2]),
            include_movies=bool(row[3]),
            include_ova=bool(row[4]),
            all_formats=bool(row[5]),
            result_ids=[int(value) for value in json.loads(row[6])],
            fetched_at_unix=float(row[7]),
            expires_at_unix=float(row[8]),
            last_error=row[9],
        )
        if fresh_only and not cached.is_fresh(now):
            return None
        return cached

    def upsert(
        self,
        *,
        query: str,
        top_k: int,
        include_movies: bool,
        include_ova: bool,
        all_formats: bool,
        result_ids: list[int],
        now: float | None = None,
        ttl_seconds: int,
    ) -> FallbackQueryRow:
        fetched_at = time.time() if now is None else now
        cache_key = query_cache_key(
            query=query,
            top_k=top_k,
            include_movies=include_movies,
            include_ova=include_ova,
            all_formats=all_formats,
        )
        self.con.execute(
            f"""
            INSERT INTO {QUERY_TABLE}
                (
                    cache_key,
                    query,
                    top_k,
                    include_movies,
                    include_ova,
                    all_formats,
                    result_ids,
                    fetched_at_unix,
                    expires_at_unix,
                    last_error
                )
            VALUES (?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?, NULL)
            ON CONFLICT (cache_key) DO UPDATE SET
                query = excluded.query,
                top_k = excluded.top_k,
                include_movies = excluded.include_movies,
                include_ova = excluded.include_ova,
                all_formats = excluded.all_formats,
                result_ids = excluded.result_ids,
                fetched_at_unix = excluded.fetched_at_unix,
                expires_at_unix = excluded.expires_at_unix,
                last_error = NULL
            """,
            [
                cache_key,
                query,
                top_k,
                include_movies,
                include_ova,
                all_formats,
                json.dumps(result_ids),
                fetched_at,
                fetched_at + ttl_seconds,
            ],
        )
        self.con.commit()
        cached = self.get(cache_key, fresh_only=False, now=fetched_at)
        if cached is None:
            raise RuntimeError("AniList fallback query cache write did not persist")
        return cached

    def record_error(self, cache_key: str, message: str) -> None:
        self.con.execute(
            f"UPDATE {QUERY_TABLE} SET last_error = ? WHERE cache_key = ?",
            [message, cache_key],
        )
        self.con.commit()


def query_cache_key(
    *,
    query: str,
    top_k: int,
    include_movies: bool,
    include_ova: bool,
    all_formats: bool,
) -> str:
    payload = {
        "query": query.strip(),
        "top_k": top_k,
        "include_movies": include_movies,
        "include_ova": include_ova,
        "all_formats": all_formats,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
