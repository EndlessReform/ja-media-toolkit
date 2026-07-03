from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from ja_media_services.anilist_search.metadata import (
    _available_columns,
    _parse_value,
    _public_columns,
)


def _git_commit_hash() -> str:
    """Return the current source commit for human-readable export filenames."""
    env_value = os.environ.get("ANILIST_SEARCH_COMMIT_HASH") or os.environ.get(
        "JA_MEDIA_GIT_COMMIT"
    )
    if env_value:
        return env_value

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[5],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def export_filename(
    *, now: datetime | None = None, commit_hash: str | None = None
) -> str:
    """Build the stable attachment filename for one point-in-time export."""
    snapshot_time = now or datetime.now(timezone.utc)
    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
    timestamp = snapshot_time.astimezone(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    commit = commit_hash or _git_commit_hash()
    return f"anilist-{timestamp}{commit}-export.jsonl"


def iter_anime_jsonl(con: duckdb.DuckDBPyConnection) -> Iterator[str]:
    """Yield one newline-delimited JSON object per exported AniList row."""
    public_columns = _public_columns(_available_columns(con))
    selected_columns = ("aid", *public_columns)
    quoted_columns = ", ".join(f'"{column}"' for column in selected_columns)
    cursor = con.execute(
        f"SELECT {quoted_columns} FROM anime ORDER BY TRY_CAST(aid AS INTEGER), aid"
    )

    while batch := cursor.fetchmany(500):
        for row in batch:
            payload = _export_payload(selected_columns, row)
            yield f"{json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n"


def _export_payload(columns: tuple[str, ...], row: tuple[Any, ...]) -> dict[str, Any]:
    payload = dict(zip(columns, row, strict=True))
    payload["anilist_id"] = int(payload.pop("aid"))
    return {
        column: _parse_value(column, value)
        for column, value in payload.items()
    }


def register_export_routes(app: FastAPI, app_state: Any) -> None:
    """Register full local AniList metadata export routes."""

    @app.get("/export.jsonl", response_model=None)
    async def export_jsonl_endpoint():
        con = app_state.con
        if con is None:
            raise HTTPException(status_code=503, detail="Index not ready")

        filename = export_filename()

        def locked_rows() -> Iterator[str]:
            with app_state._lock:
                yield from iter_anime_jsonl(con)

        return StreamingResponse(
            locked_rows(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
