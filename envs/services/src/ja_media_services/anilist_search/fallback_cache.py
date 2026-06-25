from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any

import duckdb

from ja_media_services.anilist_search.fallback_schema import (
    ANIME_TABLE,
    ensure_fallback_schema,
)

ACTIVE_OR_VOLATILE_STATUSES = frozenset({
    "RELEASING",
    "NOT_YET_RELEASED",
    "HIATUS",
})


@dataclass(frozen=True)
class FallbackAnimeRow:
    """One cached exact-ID fallback row from direct AniList lookup."""

    aid: str
    payload: dict[str, Any]
    status: str | None
    fetched_at_unix: float
    expires_at_unix: float
    last_error: str | None
    negative: bool

    def is_fresh(self, now: float | None = None) -> bool:
        return self.expires_at_unix > (time.time() if now is None else now)


@dataclass(frozen=True)
class FallbackTtlPolicy:
    """TTL settings for direct AniList fallback rows."""

    airing_seconds: int
    finished_seconds: int
    negative_seconds: int

    def for_status(self, status: str | None) -> int:
        normalized = status.strip().upper() if status else None
        if normalized == "FINISHED":
            return self.finished_seconds
        return self.airing_seconds


class AniListFallbackCache:
    """Small DuckDB-backed cache for direct AniList fallback responses."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        *,
        ttl_policy: FallbackTtlPolicy,
    ) -> None:
        self.con = con
        self.ttl_policy = ttl_policy
        ensure_fallback_schema(con)

    def get_anime(
        self,
        aid: int | str,
        *,
        fresh_only: bool = True,
        now: float | None = None,
    ) -> FallbackAnimeRow | None:
        row = self.con.execute(
            f"""
            SELECT
                aid,
                payload_json::VARCHAR,
                status,
                fetched_at_unix,
                expires_at_unix,
                last_error,
                negative
            FROM {ANIME_TABLE}
            WHERE aid = ?
            """,
            [str(aid)],
        ).fetchone()
        if row is None:
            return None
        cached = FallbackAnimeRow(
            aid=str(row[0]),
            payload=json.loads(row[1]),
            status=row[2],
            fetched_at_unix=float(row[3]),
            expires_at_unix=float(row[4]),
            last_error=row[5],
            negative=bool(row[6]),
        )
        if fresh_only and not cached.is_fresh(now):
            return None
        return cached

    def upsert_anime(
        self,
        aid: int | str,
        payload: dict[str, Any],
        *,
        now: float | None = None,
        status: str | None = None,
    ) -> FallbackAnimeRow:
        fetched_at = time.time() if now is None else now
        resolved_status = status or _string_or_none(payload.get("status"))
        expires_at = fetched_at + self.ttl_policy.for_status(resolved_status)
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.con.execute(
            f"""
            INSERT INTO {ANIME_TABLE}
                (aid, payload_json, status, fetched_at_unix, expires_at_unix, last_error, negative)
            VALUES (?, CAST(? AS JSON), ?, ?, ?, NULL, false)
            ON CONFLICT (aid) DO UPDATE SET
                payload_json = excluded.payload_json,
                status = excluded.status,
                fetched_at_unix = excluded.fetched_at_unix,
                expires_at_unix = excluded.expires_at_unix,
                last_error = NULL,
                negative = false
            """,
            [str(aid), payload_json, resolved_status, fetched_at, expires_at],
        )
        self.con.commit()
        cached = self.get_anime(aid, fresh_only=False, now=fetched_at)
        if cached is None:
            raise RuntimeError("AniList fallback cache write did not persist")
        return cached

    def upsert_negative_anime(
        self,
        aid: int | str,
        *,
        now: float | None = None,
        last_error: str | None = None,
    ) -> FallbackAnimeRow:
        fetched_at = time.time() if now is None else now
        expires_at = fetched_at + self.ttl_policy.negative_seconds
        self.con.execute(
            f"""
            INSERT INTO {ANIME_TABLE}
                (aid, payload_json, status, fetched_at_unix, expires_at_unix, last_error, negative)
            VALUES (?, CAST(? AS JSON), NULL, ?, ?, ?, true)
            ON CONFLICT (aid) DO UPDATE SET
                payload_json = excluded.payload_json,
                status = NULL,
                fetched_at_unix = excluded.fetched_at_unix,
                expires_at_unix = excluded.expires_at_unix,
                last_error = excluded.last_error,
                negative = true
            """,
            [str(aid), "{}", fetched_at, expires_at, last_error],
        )
        self.con.commit()
        cached = self.get_anime(aid, fresh_only=False, now=fetched_at)
        if cached is None:
            raise RuntimeError("AniList fallback negative cache write did not persist")
        return cached

    def record_anime_error(self, aid: int | str, message: str) -> None:
        """Attach an error to an existing row without changing cached payload."""

        self.con.execute(
            f"UPDATE {ANIME_TABLE} SET last_error = ? WHERE aid = ?",
            [message, str(aid)],
        )
        self.con.commit()

def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
