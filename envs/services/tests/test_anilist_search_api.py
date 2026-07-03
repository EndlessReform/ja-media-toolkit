from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ja_media_services.anilist_search import dataset, db
from ja_media_services.anilist_search.app import app_state, create_app
from test_anilist_search_db import write_dataset


def test_bulk_search_endpoint_returns_ordered_local_results(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(
        csv_path,
        [
            {
                "id": "1",
                "title_romaji": "Aria",
                "title_english": "Aria",
                "title_native": "ARIA",
                "format": "TV",
            },
            {
                "id": "2",
                "title_romaji": "Non Non Biyori",
                "title_english": "Non Non Biyori",
                "title_native": "のんのんびより",
                "format": "TV",
            },
        ],
    )
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    app_state.con = con
    app = create_app()
    client = TestClient(app)
    try:
        response = client.post(
            "/search/bulk",
            json={"queries": ["Aria", "missing", "Non Non"], "k": 1},
        )

        assert response.status_code == 200
        payload = response.json()
        assert [item["query"] for item in payload["results"]] == [
            "Aria",
            "missing",
            "Non Non",
        ]
        assert payload["results"][0]["results"][0]["anilist_id"] == 1
        assert payload["results"][1]["results"] == []
        assert payload["results"][2]["results"][0]["anilist_id"] == 2
    finally:
        app_state.con = None
        con.close()


def test_bulk_search_endpoint_rejects_force_anilist_field(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(
        csv_path,
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
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    app_state.con = con
    app = create_app()
    client = TestClient(app)
    try:
        response = client.post(
            "/search/bulk",
            json={"queries": ["Aria"], "force_anilist": True},
        )

        assert response.status_code == 400
        assert "local-only" in response.json()["detail"]
    finally:
        app_state.con = None
        con.close()


def test_bulk_search_endpoint_ignores_unrelated_extra_fields(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(
        csv_path,
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
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    app_state.con = con
    app = create_app()
    client = TestClient(app)
    try:
        response = client.post(
            "/search/bulk",
            json={"queries": ["Aria"], "accidental": "metadata", "k": 1},
        )

        assert response.status_code == 200
        assert response.json()["results"][0]["results"][0]["anilist_id"] == 1
    finally:
        app_state.con = None
        con.close()


def test_bulk_search_endpoint_can_stream_jsonl_shape(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(
        csv_path,
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
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    app_state.con = con
    app = create_app()
    client = TestClient(app)
    try:
        response = client.post(
            "/search/bulk?format=jsonl",
            json={"queries": ["Aria", "missing"], "k": 1},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        lines = response.text.splitlines()
        assert [json.loads(line)["query"] for line in lines] == ["Aria", "missing"]
        assert json.loads(lines[0])["results"][0]["anilist_id"] == 1
        assert json.loads(lines[1])["results"] == []
    finally:
        app_state.con = None
        con.close()
