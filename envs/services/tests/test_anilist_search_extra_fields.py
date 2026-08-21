from __future__ import annotations

import json
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from ja_media_services.anilist_search import dataset, db
from ja_media_services.anilist_search.app import app_state, create_app
from test_anilist_search_db import write_dataset


def test_search_endpoint_adds_requested_extra_fields(tmp_path: Path) -> None:
    client, con = _client_with_dataset(
        tmp_path,
        [
            {
                "id": "1",
                "title_romaji": "Aria",
                "title_english": "Aria",
                "title_native": "ARIA",
                "format": "TV",
                "averageScore": "82",
                "popularity": "101",
                "genres": json.dumps(["Slice of Life"]),
            }
        ],
    )
    try:
        response = client.get(
            "/search?query=Aria&k=1&extraFields=popularity,averageScore,genres"
        )

        assert response.status_code == 200
        candidate = response.json()[0]
        assert candidate["anilist_id"] == 1
        assert candidate["popularity"] == 101
        assert candidate["averageScore"] == 82
        assert candidate["genres"] == ["Slice of Life"]
    finally:
        app_state.con = None
        con.close()


def test_bulk_search_endpoint_adds_requested_extra_fields(tmp_path: Path) -> None:
    client, con = _client_with_dataset(
        tmp_path,
        [
            {
                "id": "1",
                "title_romaji": "Aria",
                "title_english": "Aria",
                "title_native": "ARIA",
                "format": "TV",
                "siteUrl": "https://anilist.co/anime/1",
                "favourites": "12",
            }
        ],
    )
    try:
        response = client.post(
            "/search/bulk",
            json={
                "queries": ["Aria"],
                "k": 1,
                "extraFields": ["siteUrl", "favourites"],
            },
        )

        assert response.status_code == 200
        candidate = response.json()["results"][0]["results"][0]
        assert candidate["siteUrl"] == "https://anilist.co/anime/1"
        assert candidate["favourites"] == 12
    finally:
        app_state.con = None
        con.close()


def test_search_endpoint_rejects_unknown_extra_fields(tmp_path: Path) -> None:
    client, con = _client_with_dataset(
        tmp_path,
        [
            {
                "id": "1",
                "title_romaji": "Aria",
                "title_english": "Aria",
                "title_native": "ARIA",
                "format": "TV",
            }
        ],
    )
    try:
        response = client.get("/search?query=Aria&extraFields=popularity")

        assert response.status_code == 400
        assert "Unknown AniList metadata field" in response.json()["detail"]
    finally:
        app_state.con = None
        con.close()


def _client_with_dataset(
    tmp_path: Path,
    rows: list[dict[str, str]],
) -> tuple[TestClient, duckdb.DuckDBPyConnection]:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(csv_path, rows)
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    app_state.con = con
    return TestClient(create_app()), con
