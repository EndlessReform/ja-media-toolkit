from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from ja_media_services.anilist_search.app import app_state, create_app
from ja_media_services.anilist_search import db
from ja_media_services.anilist_search.metrics import render_metrics


def write_dataset(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_fields = [
        "id",
        "title_romaji",
        "title_english",
        "title_native",
        "season",
        "seasonYear",
        "format",
        "synonyms",
        "updatedAt",
    ]
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


def test_ensure_dataset_checks_kaggle_when_csv_is_already_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / db.CSV_NAME
    csv_path.write_text("cached", encoding="utf-8")
    calls: list[tuple[str, str, str]] = []

    def download(handle: str, *, path: str, output_dir: str) -> str:
        calls.append((handle, path, output_dir))
        csv_path.write_text("current", encoding="utf-8")
        return str(csv_path)

    monkeypatch.setattr(db.kagglehub, "dataset_download", download)

    assert db.ensure_dataset(tmp_path) == csv_path
    assert calls == [(db.DATASET_HANDLE, db.CSV_NAME, str(tmp_path))]
    assert csv_path.read_text(encoding="utf-8") == "current"


def test_ensure_dataset_downloads_csv_on_first_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "data" / db.CSV_NAME

    def download(handle: str, *, path: str, output_dir: str) -> str:
        assert handle == db.DATASET_HANDLE
        assert path == db.CSV_NAME
        assert output_dir == str(csv_path.parent)
        csv_path.write_text("current", encoding="utf-8")
        return str(csv_path)

    monkeypatch.setattr(db.kagglehub, "dataset_download", download)

    assert db.ensure_dataset(csv_path.parent) == csv_path
    assert csv_path.read_text(encoding="utf-8") == "current"


def test_ensure_dataset_does_not_silently_accept_cache_when_kaggle_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / db.CSV_NAME
    csv_path.write_text("cached", encoding="utf-8")

    def fail_download(*args: object, **kwargs: object) -> str:
        raise RuntimeError("Kaggle unavailable")

    monkeypatch.setattr(db.kagglehub, "dataset_download", fail_download)

    with pytest.raises(RuntimeError, match="Kaggle unavailable"):
        db.ensure_dataset(tmp_path)


def test_build_index_rebuilds_when_dataset_signature_changes(tmp_path: Path) -> None:
    csv_path = tmp_path / db.CSV_NAME
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


def test_build_index_persists_rebuild_and_latest_dataset_update_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / db.CSV_NAME
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
                "updatedAt": "1700000000",
            },
            {
                "id": "2",
                "title_romaji": "Frieren",
                "title_english": "Frieren",
                "title_native": "葬送のフリーレン",
                "format": "TV",
                "updatedAt": "1800000000",
            },
        ],
    )
    monkeypatch.setattr(db.time, "time", lambda: 1900000000.0)

    con = db.open_db(db_path)
    try:
        db.build_index(csv_path, con)

        assert db.get_index_timestamps(con) == (1900000000.0, 1800000000.0)
    finally:
        con.close()


def test_render_metrics_distinguishes_check_rebuild_and_dataset_update() -> None:
    status = db.RefreshStatus(
        last_success_unix=1900000000.0,
        last_rebuild_unix=1890000000.0,
        dataset_latest_update_unix=1880000000.0,
    )

    families = text_string_to_metric_families(
        render_metrics(status, row_count=20346).decode()
    )
    samples = {
        sample.name: sample.value
        for family in families
        for sample in family.samples
    }

    assert samples["anilist_search_last_check_timestamp"] == 1900000000.0
    assert samples["anilist_search_last_rebuild_timestamp"] == 1890000000.0
    assert (
        samples["anilist_search_dataset_latest_update_timestamp"] == 1880000000.0
    )


def test_build_index_can_force_rebuild_existing_fts_index(tmp_path: Path) -> None:
    csv_path = tmp_path / db.CSV_NAME
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
    csv_path = tmp_path / db.CSV_NAME
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

        payload = db.fetch_anime_metadata(
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
    csv_path = tmp_path / db.CSV_NAME
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

        assert db.rebuild_from_cached_csv(csv_path, db_path, con) == 1
        payload = db.fetch_anime_metadata(con, 1535, fields=("description",))

        assert payload == {
            "description": "A notebook with consequences.",
            "anilist_id": 1535,
        }
    finally:
        con.close()


def test_anime_detail_endpoint_supports_field_filtering(tmp_path: Path) -> None:
    csv_path = tmp_path / db.CSV_NAME
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
    csv_path = tmp_path / db.CSV_NAME
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


def test_try_refresh_dataset_uses_file_signature_not_plain_download_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / db.CSV_NAME
    write_dataset(
        csv_path,
        [
            {
                "id": "1",
                "title_romaji": "Yuru Camp",
                "title_english": "Laid-Back Camp",
                "title_native": "ゆるキャン△",
                "format": "TV",
            }
        ],
    )

    def noop_download(*args: object, **kwargs: object) -> str:
        return str(csv_path)

    monkeypatch.setattr(db.kagglehub, "dataset_download", noop_download)
    assert db.try_refresh_dataset(tmp_path) is False

    def changed_download(*args: object, **kwargs: object) -> str:
        write_dataset(
            csv_path,
            [
                {
                    "id": "4",
                    "title_romaji": "Frieren",
                    "title_english": "Frieren",
                    "title_native": "葬送のフリーレン",
                    "format": "TV",
                }
            ],
        )
        return str(csv_path)

    monkeypatch.setattr(db.kagglehub, "dataset_download", changed_download)
    assert db.try_refresh_dataset(tmp_path) is True


def test_refresh_status_marks_stale_after_missed_refresh_window() -> None:
    status = db.RefreshStatus(last_success_unix=time.time() - 400, consecutive_failures=2)

    payload = status.as_dict(stale_after_seconds=300)

    assert payload["stale"] is True
    assert payload["consecutive_failures"] == 2


def test_resolve_formats_combines_movie_and_ova_flags() -> None:
    assert db.resolve_formats(include_movies=True, include_ova=True) == (
        "TV",
        "ONA",
        "TV_SHORT",
        "MOVIE",
        "OVA",
    )
