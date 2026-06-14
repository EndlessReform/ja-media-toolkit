from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ja_media_services.anime_crosswalk.app import create_app
from ja_media_services.anime_crosswalk.ingest import build_database
from ja_media_services.anime_crosswalk.settings import AnimeCrosswalkSettings


def make_client(tmp_path: Path) -> TestClient:
    source_json = tmp_path / "anime-list-full.json"
    source_json.write_text(
        json.dumps(
            [
                {
                    "type": "MOVIE",
                    "anidb_id": 5459,
                    "mal_id": 3269,
                    "tvdb_id": 79099,
                    "themoviedb_id": {"tv": 8864, "movie": 128},
                },
                {
                    "type": "TV",
                    "anidb_id": 1,
                    "mal_id": 1,
                    "tvdb_id": 111,
                    "themoviedb_id": {"tv": 222},
                },
            ]
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "anime_lists.sqlite"
    build_database(
        input_path=source_json,
        output_path=db_path,
        source_repo="Fribb/anime-lists",
        source_branch="master",
        source_commit="abc123",
    )
    settings = AnimeCrosswalkSettings(
        db_path=db_path,
        source_json_path=source_json,
        repo_root=Path(__file__).resolve().parents[3],
    )
    return TestClient(create_app(settings))


def test_lookup_endpoints_return_stable_shape(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/tvdb/movie/79099")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "tvdb"
    assert payload["id"] == "79099"
    assert payload["media_kind"] == "movie"
    assert payload["count"] == 1
    assert payload["results"][0]["mal_id"] == 3269


def test_missing_lookup_is_empty_200(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/resolve/tvdb/999999")

    assert response.status_code == 200
    assert response.json()["results"] == []


def test_bulk_lookup_preserves_order_and_no_match_payloads(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/resolve/bulk",
        json={
            "lookups": [
                {"source": "tvdb", "id": 79099, "media_kind": "movie"},
                {"source": "mal", "id": 999999},
                {"source": "tmdb", "id": 222, "media_kind": "tv"},
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 3
    assert payload["results"][0]["source"] == "tvdb"
    assert payload["results"][0]["count"] == 1
    assert payload["results"][1] == {
        "source": "mal",
        "id": "999999",
        "media_kind": None,
        "count": 0,
        "results": [],
    }
    assert payload["results"][2]["source"] == "tmdb"
    assert payload["results"][2]["results"][0]["tvdb_id"] == 111


def test_bulk_lookup_invalid_source_is_400(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post("/resolve/bulk", json={"lookups": [{"source": "nope", "id": 1}]})

    assert response.status_code == 400


def test_invalid_source_is_400(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/resolve/nope/1")

    assert response.status_code == 400


def test_llms_txt_reproduces_readme_context(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/llms.txt")

    assert response.status_code == 200
    assert response.text.startswith("# ja-media-toolkit anime crosswalk service")
    assert "## Project README" in response.text
    assert "# ja-media-toolkit" in response.text


def test_source_json_can_be_returned_gzipped(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/data/anime-list-full.json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()[0]["anidb_id"] == 5459
