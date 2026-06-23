from __future__ import annotations

import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

from ja_media_services.anilist_search.app import app_state, create_app
from ja_media_services.anilist_search import dataset, db, metadata


def write_dataset(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_fields = ["id", "title_romaji", "title_english", "title_native", "season", "seasonYear", "format", "synonyms"]
    extra_fields = [
        field
        for row in rows
        for field in row
        if field not in base_fields
    ]
    fieldnames = [*base_fields, *dict.fromkeys(extra_fields)]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({"synonyms": "[]", **row})


def test_build_index_rebuilds_when_dataset_signature_changes(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(
        csv_path,
        [
            {
                "id": "1",
                "title_romaji": "Bocchi the Rock",
                "title_english": "Bocchi the Rock",
                "title_native": "ぼっち・ざ・ろっく！",
                "format": "TV",
            }
        ],
    )

    con = db.open_db(db_path)
    try:
        assert db.build_index(csv_path, con) == 1
        assert db.search(con, "Bocchi")[0]["anilist_id"] == 1

        write_dataset(
            csv_path,
            [
                {
                    "id": "2",
                    "title_romaji": "Gintama",
                    "title_english": "Gintama",
                    "title_native": "銀魂",
                    "format": "TV",
                },
                {
                    "id": "3",
                    "title_romaji": "Non Non Biyori",
                    "title_english": "Non Non Biyori",
                    "title_native": "のんのんびより",
                    "format": "TV",
                },
            ],
        )

        assert db.rebuild_if_needed(csv_path, db_path, con) == 2
        assert db.get_row_count(con) == 2
        assert db.search(con, "Gintama")[0]["anilist_id"] == 2
    finally:
        con.close()


def test_build_index_can_force_rebuild_existing_fts_index(tmp_path: Path) -> None:
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
    try:
        assert db.build_index(csv_path, con) == 1
        assert db.build_index(csv_path, con, force=True) == 1
        assert db.search(con, "Aria")[0]["anilist_id"] == 1
    finally:
        con.close()


def test_build_index_keeps_full_csv_metadata_for_detail_lookup(tmp_path: Path) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(
        csv_path,
        [
            {
                "id": "395",
                "title_romaji": "Ginga Eiyuu Densetsu",
                "title_english": "Legend of the Galactic Heroes",
                "title_native": "銀河英雄伝説",
                "format": "OVA",
                "description": "Space opera.",
                "idMal": "820",
                "characters": json.dumps(
                    [
                        {
                            "role": "MAIN",
                            "node": {
                                "name": {
                                    "full": "Reinhard von Lohengramm",
                                    "native": "ラインハルト・フォン・ローエングラム",
                                }
                            },
                        }
                    ]
                ),
            }
        ],
    )

    con = db.open_db(db_path)
    try:
        assert db.build_index(csv_path, con) == 1

        payload = metadata.fetch_anime_metadata(
            con, 395, fields=("title_romaji", "description", "idMal", "characters")
        )

        assert payload == {
            "title_romaji": "Ginga Eiyuu Densetsu",
            "description": "Space opera.",
            "idMal": 820,
            "characters": [
                {
                    "role": "MAIN",
                    "node": {
                        "name": {
                            "full": "Reinhard von Lohengramm",
                            "native": "ラインハルト・フォン・ローエングラム",
                        }
                    },
                }
            ],
            "anilist_id": 395,
        }
    finally:
        con.close()


def test_startup_rebuild_zeroes_stale_table_even_when_csv_signature_matches(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / dataset.CSV_NAME
    db_path = tmp_path / "anime_index.db"
    write_dataset(
        csv_path,
        [
            {
                "id": "1535",
                "title_romaji": "DEATH NOTE",
                "title_english": "Death Note",
                "title_native": "DEATH NOTE",
                "format": "TV",
                "description": "A notebook with consequences.",
            }
        ],
    )

    con = db.open_db(db_path)
    try:
        assert db.build_index(csv_path, con) == 1
        con.execute("DROP SCHEMA IF EXISTS fts_main_anime CASCADE")
        con.execute("DROP TABLE anime")
        con.execute("""
            CREATE TABLE anime (
                aid VARCHAR PRIMARY KEY,
                title_romaji VARCHAR,
                title_english VARCHAR,
                title_native VARCHAR,
                format VARCHAR,
                search_text VARCHAR
            )
        """)
        con.execute("""
            INSERT INTO anime
            VALUES ('1535', 'DEATH NOTE', 'Death Note', 'DEATH NOTE', 'TV', 'Death Note')
        """)
        con.commit()

        row_count, con = db.rebuild_from_cached_csv(csv_path, db_path, con)
        assert row_count == 1
        payload = metadata.fetch_anime_metadata(con, 1535, fields=("description",))

        assert payload == {
            "description": "A notebook with consequences.",
            "anilist_id": 1535,
        }
    finally:
        con.close()


def test_anime_detail_endpoint_supports_field_filtering(tmp_path: Path) -> None:
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
                "description": "Gondola apprentices on Mars.",
            }
        ],
    )
    con = db.open_db(db_path)
    db.build_index(csv_path, con)
    app_state.con = con
    app = create_app()
    client = TestClient(app)
    try:
        response = client.get("/anime/1?fields=title_romaji,description")

        assert response.status_code == 200
        assert response.json() == {
            "title_romaji": "Aria",
            "description": "Gondola apprentices on Mars.",
            "anilist_id": 1,
        }
    finally:
        app_state.con = None
        con.close()


def test_anime_detail_endpoint_rejects_unknown_fields(tmp_path: Path) -> None:
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
        response = client.get("/anime/1?fields=nope")

        assert response.status_code == 400
        assert "Unknown AniList metadata field" in response.json()["detail"]
    finally:
        app_state.con = None
        con.close()


def test_resolve_formats_combines_movie_and_ova_flags() -> None:
    assert db.resolve_formats(include_movies=True, include_ova=True) == (
        "TV",
        "ONA",
        "TV_SHORT",
        "MOVIE",
        "OVA",
    )
