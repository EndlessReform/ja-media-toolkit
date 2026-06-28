from __future__ import annotations

import logging
from typing import Final

import duckdb

logger = logging.getLogger("ja_media_services.anilist_search.fallback_schema")

ANIME_TABLE: Final = "anilist_fallback_anime"
QUERY_TABLE: Final = "anilist_fallback_query"


def ensure_fallback_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create the durable AniList direct-fallback tables if needed.

    The CSV-derived `anime` table can be rebuilt from Kaggle at any time, but
    direct AniList lookups are local cache data. Keeping the DDL idempotent lets
    every process startup and rebuild target repair the cache schema without
    treating schema creation as a special migration phase.
    """

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {ANIME_TABLE} (
            aid VARCHAR PRIMARY KEY,
            payload_json JSON NOT NULL,
            status VARCHAR,
            fetched_at_unix DOUBLE NOT NULL,
            expires_at_unix DOUBLE NOT NULL,
            last_error VARCHAR,
            negative BOOLEAN NOT NULL DEFAULT false
        )
    """)
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {QUERY_TABLE} (
            cache_key VARCHAR PRIMARY KEY,
            query VARCHAR NOT NULL,
            top_k INTEGER NOT NULL,
            include_movies BOOLEAN NOT NULL,
            include_ova BOOLEAN NOT NULL,
            all_formats BOOLEAN NOT NULL,
            result_ids JSON NOT NULL,
            fetched_at_unix DOUBLE NOT NULL,
            expires_at_unix DOUBLE NOT NULL,
            last_error VARCHAR
        )
    """)
    con.commit()


def copy_fallback_tables(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
) -> None:
    """Copy fallback cache rows into a freshly rebuilt DuckDB database.

    Rebuilds publish a sibling database atomically. Without this explicit copy,
    a successful Kaggle refresh would replace the DB file and lose every direct
    AniList cache row even though those rows are independent of the CSV index.
    """

    ensure_fallback_schema(source)
    ensure_fallback_schema(target)
    _copy_anime_rows(source, target)
    _copy_query_rows(source, target)
    target.commit()


def _copy_anime_rows(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
) -> None:
    rows = source.execute(f"""
        SELECT
            aid,
            payload_json::VARCHAR,
            status,
            fetched_at_unix,
            expires_at_unix,
            last_error,
            negative
        FROM {ANIME_TABLE}
    """).fetchall()
    if not rows:
        return
    target.executemany(
        f"""
        INSERT INTO {ANIME_TABLE}
            (aid, payload_json, status, fetched_at_unix, expires_at_unix, last_error, negative)
        VALUES (?, CAST(? AS JSON), ?, ?, ?, ?, ?)
        ON CONFLICT (aid) DO UPDATE SET
            payload_json = excluded.payload_json,
            status = excluded.status,
            fetched_at_unix = excluded.fetched_at_unix,
            expires_at_unix = excluded.expires_at_unix,
            last_error = excluded.last_error,
            negative = excluded.negative
        """,
        rows,
    )
    logger.info("Copied AniList fallback anime cache rows (rows=%d)", len(rows))


def _copy_query_rows(
    source: duckdb.DuckDBPyConnection,
    target: duckdb.DuckDBPyConnection,
) -> None:
    rows = source.execute(f"""
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
    """).fetchall()
    if not rows:
        return
    target.executemany(
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
        VALUES (?, ?, ?, ?, ?, ?, CAST(? AS JSON), ?, ?, ?)
        ON CONFLICT (cache_key) DO UPDATE SET
            query = excluded.query,
            top_k = excluded.top_k,
            include_movies = excluded.include_movies,
            include_ova = excluded.include_ova,
            all_formats = excluded.all_formats,
            result_ids = excluded.result_ids,
            fetched_at_unix = excluded.fetched_at_unix,
            expires_at_unix = excluded.expires_at_unix,
            last_error = excluded.last_error
        """,
        rows,
    )
    logger.info("Copied AniList fallback query cache rows (rows=%d)", len(rows))
