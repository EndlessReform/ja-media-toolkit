from __future__ import annotations

import json
import logging
from typing import Any

import duckdb

JSON_COLUMNS = frozenset({
    "airingSchedule",
    "characters",
    "externalLinks",
    "genres",
    "nextAiringEpisode",
    "rankings",
    "recommendations",
    "relations",
    "reviews",
    "staff",
    "stats_scoreDistribution",
    "stats_statusDistribution",
    "streamingEpisodes",
    "studios",
    "synonyms",
    "tags",
})
RESERVED_DETAIL_COLUMNS = frozenset({"aid", "search_text"})
PRIVATE_FIELD_PREFIX = "_fallback_"

logger = logging.getLogger("ja_media_services.anilist_search.metadata")


def _available_columns(con: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
    rows = con.execute("PRAGMA table_info('anime')").fetchall()
    return tuple(str(row[1]) for row in rows)


def _requested_columns(
    con: duckdb.DuckDBPyConnection, requested_fields: tuple[str, ...] | None
) -> tuple[str, ...]:
    available = _available_columns(con)
    available_set = set(available)
    public_columns = tuple(_public_columns(available))
    if requested_fields is None:
        return public_columns

    unknown = sorted(set(requested_fields) - available_set)
    forbidden = sorted(set(requested_fields) & RESERVED_DETAIL_COLUMNS)
    if unknown or forbidden:
        bad = ", ".join([*unknown, *forbidden])
        raise ValueError(f"Unknown AniList metadata field(s): {bad}")
    return tuple(requested_fields)


def _public_columns(columns: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(
        column
        for column in columns
        if column not in RESERVED_DETAIL_COLUMNS
        and not column.startswith(PRIVATE_FIELD_PREFIX)
        and column != "id"
    )


def _parse_value(column: str, value: Any) -> Any:
    if value is None or column not in JSON_COLUMNS or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        logger.warning("AniList metadata column %s did not contain valid JSON", column)
        return value


def fetch_anime_metadata(
    con: duckdb.DuckDBPyConnection,
    anilist_id: int | str,
    *,
    fields: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Return one validated metadata projection by AniList ID.

    This broad escape hatch supports evolving downstream workflows without
    exposing arbitrary SQL column selection. Known JSON columns are decoded at
    the service boundary so SDK callers receive their natural wire shape.
    """
    columns = _requested_columns(con, fields)
    select_columns = ["aid", *columns]
    quoted_columns = ", ".join(f'"{column}"' for column in select_columns)
    row = con.execute(
        f"SELECT {quoted_columns} FROM anime WHERE aid = ?", [str(anilist_id)]
    ).fetchone()
    if row is None:
        return None
    payload = dict(zip(select_columns, row, strict=True))
    payload["anilist_id"] = int(payload.pop("aid"))
    return {
        column: _parse_value(column, value)
        for column, value in payload.items()
    }


def anime_metadata_exists(
    con: duckdb.DuckDBPyConnection,
    anilist_id: int | str,
) -> bool:
    """Return whether the CSV-backed table has an AniList ID."""

    row = con.execute(
        "SELECT 1 FROM anime WHERE aid = ? LIMIT 1",
        [str(anilist_id)],
    ).fetchone()
    return row is not None


def project_metadata_payload(
    payload: dict[str, Any],
    *,
    anilist_id: int | str,
    fields: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Project a CSV-shaped payload from fallback storage for API response."""

    public_payload = {
        column: value
        for column, value in payload.items()
        if column in _public_columns(list(payload))
    }
    if fields is None:
        selected = tuple(public_payload)
    else:
        public_columns = set(public_payload)
        unknown = sorted(set(fields) - public_columns)
        forbidden = sorted(
            field
            for field in fields
            if field in RESERVED_DETAIL_COLUMNS
            or field == "id"
            or field.startswith(PRIVATE_FIELD_PREFIX)
        )
        if unknown or forbidden:
            bad = ", ".join([*unknown, *forbidden])
            raise ValueError(f"Unknown AniList metadata field(s): {bad}")
        selected = fields

    result = {
        column: _parse_value(column, public_payload[column])
        for column in selected
    }
    result["anilist_id"] = int(anilist_id)
    return result
