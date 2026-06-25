from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from ja_media_services.anilist_search import dataset, db
from ja_media_services.anilist_search.anilist_api import AniListApiError
from ja_media_services.anilist_search.app import app_state, create_app
from ja_media_services.anilist_search.exact_fallback import resolve_exact_fallback
from ja_media_services.anilist_search.fallback_cache import (
    AniListFallbackCache,
    FallbackTtlPolicy,
)
from ja_media_services.anilist_search.observability import FallbackObserver
from ja_media_services.anilist_search.singleflight import ExactIdSingleFlight


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
        writer.writerow(
            {
                "id": "1",
                "title_romaji": "Local Anime",
                "title_english": "Local Anime",
                "title_native": "ローカル",
                "format": "TV",
                "synonyms": "[]",
            }
        )


def media_payload(anilist_id: int = 2026) -> dict[str, Any]:
    return {
        "id": anilist_id,
        "idMal": 9001,
        "title": {
            "romaji": "Future Anime",
            "english": None,
            "native": "未来アニメ",
            "userPreferred": "Future Anime",
        },
        "format": "TV",
        "status": "NOT_YET_RELEASED",
        "description": "A fallback row.",
        "synonyms": ["Future Show"],
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
    def __init__(
        self,
        results: list[dict[str, Any] | None | BaseException],
        *,
        delay_seconds: float = 0,
    ) -> None:
        self.results = results
        self.delay_seconds = delay_seconds
        self.calls = 0

    async def fetch_media_by_id(self, anilist_id: int) -> dict[str, Any] | None:
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(result, BaseException):
            raise result
        return result


def configure_app_state(con: object, client: object) -> None:
    app_state.con = con  # type: ignore[assignment]
    app_state.anilist_client = client  # type: ignore[assignment]
    app_state.fallback_ttl_policy = ttl_policy()
    app_state.fallback_observer = FallbackObserver()
    app_state.exact_id_singleflight = ExactIdSingleFlight()


def reset_app_state() -> None:
    app_state.con = None
    app_state.anilist_client = None
    app_state.fallback_ttl_policy = None
    app_state.fallback_observer = FallbackObserver()
    app_state.exact_id_singleflight = ExactIdSingleFlight()


def test_anime_detail_exact_fallback_fetches_and_caches(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(csv_path)
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    client = FakeAniListClient([media_payload()])
    configure_app_state(con, client)
    api = TestClient(create_app())
    try:
        response = api.get(
            "/anime/2026?fields=title_romaji,status,synonyms,nextAiringEpisode"
        )
        cached_response = api.get("/anime/2026?fields=title_romaji,status")

        assert response.status_code == 200
        assert response.json() == {
            "title_romaji": "Future Anime",
            "status": "NOT_YET_RELEASED",
            "synonyms": ["Future Show"],
            "nextAiringEpisode": None,
            "anilist_id": 2026,
        }
        assert cached_response.status_code == 200
        assert cached_response.json()["title_romaji"] == "Future Anime"
        assert client.calls == 1
        fallback = api.get("/healthz").json()["fallback"]
        metrics = api.get("/metrics").text
        assert fallback["exact_requests"] == 2
        assert fallback["exact_cache_hits"] == 1
        assert fallback["exact_cache_misses"] == 1
        assert fallback["outbound_requests"] == 1
        assert fallback["exact_hit_rate"] == 0.5
        assert 'anilist_search_fallback_requests_total{kind="exact"} 2.0' in metrics
    finally:
        reset_app_state()
        con.close()


def test_anime_detail_exact_fallback_negative_cache_avoids_refetch(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(csv_path)
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    client = FakeAniListClient([None])
    configure_app_state(con, client)
    api = TestClient(create_app())
    try:
        first = api.get("/anime/4040")
        second = api.get("/anime/4040")

        assert first.status_code == 404
        assert second.status_code == 404
        assert client.calls == 1
    finally:
        reset_app_state()
        con.close()


@pytest.mark.asyncio
async def test_exact_fallback_coalesces_concurrent_misses(tmp_path: Path) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    client = FakeAniListClient([media_payload()], delay_seconds=0.05)
    singleflight = ExactIdSingleFlight()
    try:
        results = await asyncio.gather(
            *[
                resolve_exact_fallback(
                    anilist_id=2026,
                    fields=("title_romaji",),
                    con=con,
                    db_lock=app_state._lock,
                    client=client,
                    ttl_policy=ttl_policy(),
                    singleflight=singleflight,
                    now=100,
                )
                for _ in range(5)
            ]
        )

        assert [result["title_romaji"] for result in results] == ["Future Anime"] * 5
        assert client.calls == 1
        assert singleflight.coalesced_waits == 4
    finally:
        con.close()


@pytest.mark.asyncio
async def test_exact_fallback_returns_stale_row_on_refresh_error(
    tmp_path: Path,
) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    cache = AniListFallbackCache(con, ttl_policy=ttl_policy())
    cache.upsert_anime(2026, {"id": 2026, "title_romaji": "Stale"}, now=100)
    client = FakeAniListClient([AniListApiError("upstream unavailable", 503)])
    try:
        result = await resolve_exact_fallback(
            anilist_id=2026,
            fields=("title_romaji",),
            con=con,
            db_lock=app_state._lock,
            client=client,
            ttl_policy=ttl_policy(),
            singleflight=ExactIdSingleFlight(),
            now=200,
        )

        cached = cache.get_anime(2026, fresh_only=False)
        assert result == {"title_romaji": "Stale", "anilist_id": 2026}
        assert cached is not None
        assert cached.last_error == "upstream unavailable"
    finally:
        con.close()


@pytest.mark.asyncio
async def test_exact_fallback_honors_retry_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    con = db.open_db(tmp_path / "anime_index.db")
    client = FakeAniListClient([
        AniListApiError("limited", 429, retry_after_seconds=1),
        media_payload(),
    ])
    sleeps: list[float] = []

    async def fake_sleep_for_retry_after(error: AniListApiError) -> None:
        sleeps.append(error.retry_after_seconds or 0)

    monkeypatch.setattr(
        "ja_media_services.anilist_search.exact_fallback.sleep_for_retry_after",
        fake_sleep_for_retry_after,
    )
    try:
        result = await resolve_exact_fallback(
            anilist_id=2026,
            fields=("title_romaji",),
            con=con,
            db_lock=app_state._lock,
            client=client,
            ttl_policy=ttl_policy(),
            singleflight=ExactIdSingleFlight(),
            now=100,
        )

        assert result["title_romaji"] == "Future Anime"
        assert client.calls == 2
        assert sleeps == [1]
    finally:
        con.close()
