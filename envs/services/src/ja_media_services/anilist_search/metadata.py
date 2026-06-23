from __future__ import annotations

import json
import logging
from typing import Any

import duckdb

JSON_COLUMNS = frozenset({"characters", "relations", "staff", "studios", "synonyms"})
RESERVED_DETAIL_COLUMNS = frozenset({"aid", "search_text"})

logger = logging.getLogger("ja_media_services.anilist_search.metadata")


def _available_columns(con: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
    rows = con.execute("PRAGMA table_info('anime')").fetchall()
    return tuple(str(row[1]) for row in rows)


def _requested_columns(
    con: duckdb.DuckDBPyConnection, requested_fields: tuple[str, ...] | None
) -> tuple[str, ...]:
    available = _available_columns(con)
    available_set = set(available)
    public_columns = tuple(
        column for column in available if column not in RESERVED_DETAIL_COLUMNS
    )
    if requested_fields is None:
        return public_columns

    unknown = sorted(set(requested_fields) - available_set)
    forbidden = sorted(set(requested_fields) & RESERVED_DETAIL_COLUMNS)
    if unknown or forbidden:
        bad = ", ".join([*unknown, *forbidden])
        raise ValueError(f"Unknown AniList metadata field(s): {bad}")
    return tuple(requested_fields)


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
