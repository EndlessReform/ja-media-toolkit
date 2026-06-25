from __future__ import annotations

import csv
from pathlib import Path

from ja_media_services.anilist_search import dataset, db
from ja_media_services.anilist_search.fallback_cache import (
    AniListFallbackCache,
    FallbackTtlPolicy,
)
from ja_media_services.anilist_search.fallback_query_cache import (
    AniListFallbackQueryCache,
)
from ja_media_services.anilist_search.fallback_schema import (
    ANIME_TABLE,
    QUERY_TABLE,
)


def write_dataset(path: Path, *, title: str, anilist_id: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "id",
                "title_romaji",
                "title_english",
                "title_native",
                "season",
                "seasonYear",
                "format",
                "synonyms",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "id": str(anilist_id),
                "title_romaji": title,
                "title_english": title,
                "title_native": title,
                "format": "TV",
                "synonyms": "[]",
            }
        )


def policy() -> FallbackTtlPolicy:
    return FallbackTtlPolicy(
        airing_seconds=7,
        finished_seconds=30,
        negative_seconds=1,
    )


def test_open_db_creates_fallback_tables(tmp_path: Path) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    try:
        tables = {
            row[0]
            for row in con.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

        assert ANIME_TABLE in tables
        assert QUERY_TABLE in tables
    finally:
        con.close()


def test_exact_cache_uses_status_ttls_and_negative_ttl(tmp_path: Path) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    cache = AniListFallbackCache(con, ttl_policy=policy())
    try:
        finished = cache.upsert_anime(
            10,
            {"id": 10, "status": "FINISHED", "title_romaji": "Finished"},
            now=100,
        )
        active = cache.upsert_anime(
            11,
            {"id": 11, "status": "RELEASING", "title_romaji": "Airing"},
            now=100,
        )
        negative = cache.upsert_negative_anime(12, now=100, last_error="not found")

        assert finished.expires_at_unix == 130
        assert active.expires_at_unix == 107
        assert negative.expires_at_unix == 101
        assert negative.negative is True
        assert cache.get_anime(11, now=108) is None
        assert cache.get_anime(11, fresh_only=False, now=108) == active
    finally:
        con.close()


def test_record_error_preserves_cached_payload(tmp_path: Path) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    cache = AniListFallbackCache(con, ttl_policy=policy())
    try:
        cache.upsert_anime(
            20,
            {"id": 20, "status": "FINISHED", "title_romaji": "Still Good"},
            now=100,
        )

        cache.record_anime_error(20, "429 Retry-After=60")

        cached = cache.get_anime(20, fresh_only=False)
        assert cached is not None
        assert cached.payload["title_romaji"] == "Still Good"
        assert cached.last_error == "429 Retry-After=60"
        assert cached.negative is False
    finally:
        con.close()


def test_query_cache_round_trips_direct_search_ids(tmp_path: Path) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    cache = AniListFallbackQueryCache(con)
    try:
        row = cache.upsert(
            query="Test Show",
            top_k=3,
            include_movies=False,
            include_ova=True,
            all_formats=False,
            result_ids=[100, 101],
            now=200,
            ttl_seconds=10,
        )

        cached = cache.get(row.cache_key, now=201)
        assert cached is not None
        assert cached.result_ids == [100, 101]
        assert cached.include_ova is True
        assert cache.get(row.cache_key, now=211) is None
    finally:
        con.close()


def test_rebuild_preserves_fallback_rows_across_reopen(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(csv_path, title="Local One")

    con = db.open_db(db_path)
    try:
        db.build_index(csv_path, con)
        exact_cache = AniListFallbackCache(con, ttl_policy=policy())
        query_cache = AniListFallbackQueryCache(con)
        exact_cache.upsert_anime(
            2026,
            {"id": 2026, "status": "FINISHED", "title_romaji": "Future Cache"},
            now=300,
        )
        query_row = query_cache.upsert(
            query="Future Cache",
            top_k=1,
            include_movies=False,
            include_ova=False,
            all_formats=False,
            result_ids=[2026],
            now=300,
            ttl_seconds=30,
        )

        write_dataset(csv_path, title="Local Two", anilist_id=2)
        _, con = db.rebuild_from_cached_csv(csv_path, db_path, con)
        con.close()
        con = db.open_db(db_path)

        exact_cache = AniListFallbackCache(con, ttl_policy=policy())
        query_cache = AniListFallbackQueryCache(con)
        cached_anime = exact_cache.get_anime(2026, now=301)
        cached_query = query_cache.get(query_row.cache_key, now=301)

        assert cached_anime is not None
        assert cached_anime.payload["title_romaji"] == "Future Cache"
        assert cached_query is not None
        assert cached_query.result_ids == [2026]
        assert db.search(con, "Local Two")[0]["anilist_id"] == 2
    finally:
        con.close()
