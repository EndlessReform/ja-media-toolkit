from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from ja_media_services.anilist_search import dataset, db
from ja_media_services.anilist_search.anilist_api import AniListApiError
from ja_media_services.anilist_search.app import app_state, create_app
from ja_media_services.anilist_search.fallback_cache import FallbackTtlPolicy
from ja_media_services.anilist_search.observability import FallbackObserver
from ja_media_services.anilist_search.search_fallback import resolve_search_fallback


def write_dataset(path: Path) -> None:
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
        writer.writerow({
            "id": "1",
            "title_romaji": "Local Anime",
            "title_english": "Local Anime",
            "title_native": "ローカル",
            "format": "TV",
            "synonyms": "[]",
        })


def media_payload(anilist_id: int = 169580, *, format_: str = "TV") -> dict[str, Any]:
    return {
        "id": anilist_id,
        "title": {
            "romaji": "Class de 2-banme ni Kawaii Onnanoko to Tomodachi ni Natta",
            "english": None,
            "native": "クラスで2番目に可愛い女の子と友だちになった",
            "userPreferred": "Class de 2-banme",
        },
        "format": format_,
        "status": "NOT_YET_RELEASED",
        "season": "WINTER",
        "seasonYear": 2026,
        "synonyms": [],
        "trailer": None,
        "coverImage": None,
        "nextAiringEpisode": None,
        "characters": None,
        "relations": None,
        "staff": None,
        "studios": None,
        "airingSchedule": None,
        "recommendations": None,
        "reviews": None,
        "stats": None,
    }


def ttl_policy() -> FallbackTtlPolicy:
    return FallbackTtlPolicy(
        airing_seconds=7,
        finished_seconds=30,
        negative_seconds=1,
    )


class FakeAniListClient:
    def __init__(self, results: list[list[dict[str, Any]] | BaseException]) -> None:
        self.results = results
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    async def search_media(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int = 10,
    ) -> list[dict[str, Any]]:
        self.calls += 1
        self.requests.append({"query": query, "page": page, "per_page": per_page})
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(result, BaseException):
            raise result
        return result


def configure_app_state(con: object, client: object) -> None:
    app_state.con = con  # type: ignore[assignment]
    app_state.anilist_client = client  # type: ignore[assignment]
    app_state.fallback_ttl_policy = ttl_policy()
    app_state.fallback_observer = FallbackObserver()


def reset_app_state() -> None:
    app_state.con = None
    app_state.anilist_client = None
    app_state.fallback_ttl_policy = None
    app_state.fallback_observer = FallbackObserver()


def test_search_defaults_to_local_bm25_without_anilist_call(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(csv_path)
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    client = FakeAniListClient([[media_payload()]])
    configure_app_state(con, client)
    api = TestClient(create_app())
    try:
        response = api.get("/search?query=Local&k=1")

        assert response.status_code == 200
        assert response.json()[0]["anilist_id"] == 1
        assert client.calls == 0
    finally:
        reset_app_state()
        con.close()


def test_forced_search_fetches_anilist_and_caches_query(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(csv_path)
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    client = FakeAniListClient([[media_payload()]])
    configure_app_state(con, client)
    api = TestClient(create_app())
    try:
        path = "/search?query=Class+de+2-banme+ni+Kawaii&k=1&force_anilist=true"
        first = api.get(path)
        second = api.get(path)

        assert first.status_code == 200
        assert first.json()[0]["anilist_id"] == 169580
        assert first.json()[0]["title_romaji"].startswith("Class de 2-banme")
        assert second.status_code == 200
        assert second.json()[0]["anilist_id"] == 169580
        assert client.calls == 1
        fallback = api.get("/healthz").json()["fallback"]
        metrics = api.get("/metrics").text
        assert fallback["cached_rows"] == 1
        assert fallback["search_requests"] == 2
        assert fallback["search_cache_hits"] == 1
        assert fallback["search_cache_misses"] == 1
        assert fallback["outbound_requests"] == 1
        assert fallback["search_hit_rate"] == 0.5
        assert 'anilist_search_fallback_requests_total{kind="search"} 2.0' in metrics
        assert "anilist_search_fallback_cached_rows 1.0" in metrics
    finally:
        reset_app_state()
        con.close()


@pytest.mark.asyncio
async def test_forced_search_returns_stale_query_on_refresh_error(
    tmp_path: Path,
) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    client = FakeAniListClient([[media_payload()]])
    try:
        fresh = await resolve_search_fallback(
            query="Class de 2-banme",
            top_k=1,
            include_movies=False,
            include_ova=False,
            all_formats=False,
            con=con,
            db_lock=app_state._lock,
            client=client,
            ttl_policy=ttl_policy(),
            now=100,
        )
        failing = FakeAniListClient([AniListApiError("upstream unavailable", 503)])
        stale = await resolve_search_fallback(
            query="Class de 2-banme",
            top_k=1,
            include_movies=False,
            include_ova=False,
            all_formats=False,
            con=con,
            db_lock=app_state._lock,
            client=failing,
            ttl_policy=ttl_policy(),
            now=200,
        )

        assert fresh[0]["anilist_id"] == 169580
        assert stale[0]["anilist_id"] == 169580
        assert failing.calls == 1
    finally:
        con.close()


@pytest.mark.asyncio
async def test_forced_search_honors_retry_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    client = FakeAniListClient([
        AniListApiError("limited", 429, retry_after_seconds=1),
        [media_payload()],
    ])
    sleeps: list[float] = []

    async def fake_sleep_for_retry_after(error: AniListApiError) -> None:
        sleeps.append(error.retry_after_seconds or 0)

    monkeypatch.setattr(
        "ja_media_services.anilist_search.search_fallback.sleep_for_retry_after",
        fake_sleep_for_retry_after,
    )
    try:
        results = await resolve_search_fallback(
            query="Class de 2-banme",
            top_k=1,
            include_movies=False,
            include_ova=False,
            all_formats=False,
            con=con,
            db_lock=app_state._lock,
            client=client,
            ttl_policy=ttl_policy(),
            now=100,
        )

        assert results[0]["anilist_id"] == 169580
        assert client.calls == 2
        assert sleeps == [1]
    finally:
        con.close()
