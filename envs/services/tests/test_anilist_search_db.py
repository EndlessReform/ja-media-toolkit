from __future__ import annotations

import time
from pathlib import Path

import pytest

from ja_media_services.anilist_search import db


def write_dataset(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "id,title_romaji,title_english,title_native,format,synonyms",
        *[
            ",".join(
                [
                    row["id"],
                    row["title_romaji"],
                    row["title_english"],
                    row["title_native"],
                    row["format"],
                    row.get("synonyms", "[]"),
                ]
            )
            for row in rows
        ],
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
