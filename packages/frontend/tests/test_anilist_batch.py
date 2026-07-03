from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ja_media_core.anilist_search import (
    BulkSearchResponse,
    BulkSearchResult,
    SearchResponse,
    SearchResult,
)
from ja_media_frontend.anilist_batch import (
    batch_output_path,
    read_batch_items,
    run_batch_search,
)


class FakeAniListClient:
    def __init__(self) -> None:
        self.bulk_requests: list[dict[str, Any]] = []
        self.search_requests: list[dict[str, Any]] = []

    def search_bulk(
        self,
        queries: list[str] | tuple[str, ...],
        *,
        top_k: int = 3,
        include_movies: bool = False,
        include_ova: bool = False,
        all_formats: bool = False,
        extra_fields: tuple[str, ...] | None = None,
    ) -> BulkSearchResponse:
        self.bulk_requests.append({
            "queries": list(queries),
            "top_k": top_k,
            "extra_fields": extra_fields,
        })
        return BulkSearchResponse(
            results=tuple(
                BulkSearchResult(query=query, results=(_candidate(query),))
                for query in queries
            )
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        include_movies: bool = False,
        include_ova: bool = False,
        all_formats: bool = False,
        force_anilist: bool = False,
        extra_fields: tuple[str, ...] | None = None,
    ) -> SearchResponse:
        self.search_requests.append({
            "query": query,
            "top_k": top_k,
            "force_anilist": force_anilist,
            "extra_fields": extra_fields,
        })
        return SearchResponse(results=(_candidate(query),))


def test_txt_batch_writes_query_jsonl_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "blog_titles.txt"
    path.write_text("Aria\n\nNon Non Biyori\n", encoding="utf-8")
    client = FakeAniListClient()

    output = run_batch_search(
        client=client,
        path=path,
        top_k=5,
        include_movies=False,
        include_ova=False,
        all_formats=True,
        force_anilist=False,
        extra_fields=("popularity",),
    )

    assert output == tmp_path / "blog_titles.anilist.jsonl"
    assert client.bulk_requests == [
        {
            "queries": ["Aria", "Non Non Biyori"],
            "top_k": 5,
            "extra_fields": ("popularity",),
        }
    ]
    rows = _jsonl(output)
    assert rows[0]["query"] == "Aria"
    assert rows[0]["anilist_candidates"][0]["popularity"] == 100


def test_csv_batch_preserves_columns_and_adds_candidates(tmp_path: Path) -> None:
    path = tmp_path / "reviews.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["title", "sentiment"])
        writer.writeheader()
        writer.writerow({"title": "Aria", "sentiment": "glowing"})

    output = run_batch_search(
        client=FakeAniListClient(),
        path=path,
        top_k=3,
        include_movies=False,
        include_ova=False,
        all_formats=False,
        force_anilist=False,
        extra_fields=(),
    )

    row = _jsonl(output)[0]
    assert row["title"] == "Aria"
    assert row["sentiment"] == "glowing"
    assert row["query"] == "Aria"
    assert row["anilist_candidates"][0]["title_romaji"] == "Aria"


def test_jsonl_batch_can_force_per_query_search(tmp_path: Path) -> None:
    path = tmp_path / "reviews.jsonl"
    path.write_text('{"name":"Aria","rating":10}\n', encoding="utf-8")
    client = FakeAniListClient()

    run_batch_search(
        client=client,
        path=path,
        top_k=1,
        include_movies=False,
        include_ova=False,
        all_formats=False,
        force_anilist=True,
        extra_fields=("averageScore",),
    )

    assert client.bulk_requests == []
    assert client.search_requests == [
        {
            "query": "Aria",
            "top_k": 1,
            "force_anilist": True,
            "extra_fields": ("averageScore",),
        }
    ]


def test_batch_output_path_replaces_input_suffix(tmp_path: Path) -> None:
    assert batch_output_path(tmp_path / "reviews.csv") == (
        tmp_path / "reviews.anilist.jsonl"
    )


def test_unknown_record_shape_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "reviews.jsonl"
    path.write_text('{"rating":10,"body":"nice"}\n', encoding="utf-8")

    try:
        read_batch_items(path)
    except ValueError as exc:
        assert "Could not resolve a title query" in str(exc)
    else:
        raise AssertionError("expected missing title column to be rejected")


def _candidate(query: str) -> SearchResult:
    return SearchResult(
        anilist_id=1,
        title_english=query,
        title_native=query,
        title_romaji=query,
        season=None,
        season_year=None,
        format="TV",
        score=1.0,
        extra_fields={"popularity": 100},
    )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
