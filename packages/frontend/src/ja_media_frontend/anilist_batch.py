from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ja_media_core.anilist_search import (
    AniListSearchClient,
    BulkSearchResponse,
    SearchResponse,
    SearchResult,
)

BATCH_SUFFIXES = frozenset({".txt", ".csv", ".jsonl", ".ndjson"})
QUERY_COLUMNS = (
    "query",
    "title",
    "name",
    "anime_title",
    "review_title",
    "title_english",
    "title_romaji",
    "title_native",
)


@dataclass(frozen=True)
class BatchSearchItem:
    """One input record plus the title query resolved from it."""

    query: str
    record: dict[str, Any]


def is_batch_input(path: Path) -> bool:
    """Return whether a file should be treated as analytical batch input."""
    return path.suffix.lower() in BATCH_SUFFIXES


def batch_output_path(path: Path) -> Path:
    """Return the JSONL sidecar path for an analytical title batch."""
    return path.with_name(f"{path.stem}.anilist.jsonl")


def read_batch_items(path: Path) -> list[BatchSearchItem]:
    """Read TXT, CSV, or JSONL search inputs into normalized records."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return _read_txt(path)
    if suffix == ".csv":
        return _read_csv(path)
    if suffix in {".jsonl", ".ndjson"}:
        return _read_jsonl(path)
    raise ValueError(f"Unsupported batch input type: {path.suffix}")


def write_batch_results(
    *,
    path: Path,
    items: list[BatchSearchItem],
    responses: Iterable[SearchResponse],
) -> Path:
    """Write input records plus AniList candidates to a JSONL sidecar."""
    output_path = batch_output_path(path)
    with output_path.open("w", encoding="utf-8") as file:
        for item, response in zip(items, responses, strict=True):
            record = dict(item.record)
            record.setdefault("query", item.query)
            record["anilist_candidates"] = [
                search_result_to_mapping(result)
                for result in response.results
            ]
            file.write(f"{json.dumps(record, ensure_ascii=False)}\n")
    return output_path


def run_batch_search(
    *,
    client: AniListSearchClient,
    path: Path,
    top_k: int,
    include_movies: bool,
    include_ova: bool,
    all_formats: bool,
    force_anilist: bool,
    extra_fields: tuple[str, ...],
) -> Path:
    """Resolve every query from an analytical input file and write JSONL."""
    items = read_batch_items(path)
    queries = [item.query for item in items]
    if not items:
        return write_batch_results(path=path, items=[], responses=[])
    if force_anilist:
        responses = [
            client.search(
                query,
                top_k=top_k,
                include_movies=include_movies,
                include_ova=include_ova,
                all_formats=all_formats,
                force_anilist=True,
                extra_fields=extra_fields,
            )
            for query in queries
        ]
    else:
        bulk = client.search_bulk(
            queries,
            top_k=top_k,
            include_movies=include_movies,
            include_ova=include_ova,
            all_formats=all_formats,
            extra_fields=extra_fields,
        )
        responses = _responses_from_bulk(bulk)
    return write_batch_results(path=path, items=items, responses=responses)


def search_result_to_mapping(result: SearchResult) -> dict[str, Any]:
    """Serialize a search candidate, including opt-in metadata fields."""
    payload = {
        "anilist_id": result.anilist_id,
        "title_english": result.title_english,
        "title_native": result.title_native,
        "title_romaji": result.title_romaji,
        "season": result.season,
        "season_year": result.season_year,
        "format": result.format,
        "score": result.score,
    }
    payload.update(result.extra_fields)
    return payload


def _read_txt(path: Path) -> list[BatchSearchItem]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        query = line.strip()
        if query:
            items.append(BatchSearchItem(query=query, record={"query": query}))
    return items


def _read_csv(path: Path) -> list[BatchSearchItem]:
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return [
            BatchSearchItem(query=_query_from_record(row), record=dict(row))
            for row in reader
        ]


def _read_jsonl(path: Path) -> list[BatchSearchItem]:
    items = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, str):
            record = {"query": payload}
        elif isinstance(payload, dict):
            record = dict(payload)
        else:
            raise ValueError(f"JSONL line {line_number} must be an object or string")
        items.append(BatchSearchItem(query=_query_from_record(record), record=record))
    return items


def _query_from_record(record: dict[str, Any]) -> str:
    for column in QUERY_COLUMNS:
        value = record.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()
    if len(record) == 1:
        value = next(iter(record.values()))
        if value is not None and str(value).strip():
            return str(value).strip()
    raise ValueError(
        "Could not resolve a title query from record; expected one of "
        f"{', '.join(QUERY_COLUMNS)}"
    )


def _responses_from_bulk(bulk: BulkSearchResponse) -> list[SearchResponse]:
    return [
        SearchResponse(results=result.results)
        for result in bulk.results
    ]
