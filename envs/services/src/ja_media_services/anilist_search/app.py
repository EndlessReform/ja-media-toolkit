from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from ja_media_services.anilist_search.db import (
    RefreshStatus,
    background_refresh,
    ensure_dataset,
    fetch_anime_metadata,
    get_index_timestamps,
    get_row_count,
    open_db,
    rebuild_from_cached_csv,
    resolve_formats,
    search,
)

logger = logging.getLogger("ja_media_services.anilist_search")


class AppState:
    """Holds the persistent DuckDB connection and data paths."""
    con: duckdb.DuckDBPyConnection | None = None
    csv_path: Path | None = None
    db_path: Path | None = None
    data_dir: Path | None = None
    update_interval_seconds: int = 3600
    _lock = threading.Lock()
    refresh_status = RefreshStatus()


app_state = AppState()


def _get_settings():
    from ja_media_services.anilist_search.settings import AniListSearchSettings
    return AniListSearchSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = _get_settings()
    data_dir = settings.data_dir
    db_path = settings.db_path

    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = ensure_dataset(data_dir)

    con = open_db(db_path)
    with app_state._lock:
        row_count = rebuild_from_cached_csv(csv_path, db_path, con)
        last_rebuild_unix, dataset_latest_update_unix = get_index_timestamps(con)

    app_state.con = con
    app_state.csv_path = csv_path
    app_state.db_path = db_path
    app_state.data_dir = data_dir
    app_state.update_interval_seconds = settings.update_interval_seconds
    app_state.refresh_status.last_attempt_unix = time.time()
    app_state.refresh_status.last_success_unix = app_state.refresh_status.last_attempt_unix
    app_state.refresh_status.last_failure_unix = None
    app_state.refresh_status.last_failure = None
    app_state.refresh_status.consecutive_failures = 0
    app_state.refresh_status.last_rebuild_unix = last_rebuild_unix
    app_state.refresh_status.dataset_latest_update_unix = (
        dataset_latest_update_unix
    )
    app_state.refresh_status.last_index_rows = row_count

    t = threading.Thread(
        target=background_refresh,
        args=(
            data_dir,
            db_path,
            con,
            app_state._lock,
            app_state.refresh_status,
            settings.update_interval_seconds,
        ),
        daemon=True,
    )
    t.start()
    logger.info(
        "Server started; background refresh enabled (interval_seconds=%d rows=%d)",
        settings.update_interval_seconds,
        row_count,
    )

    yield

    con.close()
    app_state.con = None


def create_app() -> FastAPI:
    app = FastAPI(
        title="AniList Anime Search",
        description="BM25 fuzzy search over the AniList anime dataset, resolved to AniList IDs.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/search")
    def search_endpoint(
        query: str = Query(..., min_length=1, description="Anime name to search for"),
        k: int = Query(3, ge=1, le=50, description="Number of results (default: 3)"),
        include_movies: bool = Query(False, description="Include MOVIE format"),
        include_ova: bool = Query(False, description="Include OVA format"),
        all_formats: bool = Query(False, description="Include all formats"),
    ) -> list[dict]:
        con = app_state.con
        if con is None:
            raise HTTPException(status_code=503, detail="Index not ready")

        formats = resolve_formats(include_movies, include_ova, all_formats)
        with app_state._lock:
            results = search(con, query, k, formats)
        return results

    @app.get("/anime/{anilist_id}")
    def anime_detail_endpoint(
        anilist_id: int,
        fields: str | None = Query(
            None,
            description=(
                "Comma-separated CSV column names to return, for example "
                "'title_romaji,description,characters'. Omit for the full row."
            ),
        ),
    ) -> dict:
        con = app_state.con
        if con is None:
            raise HTTPException(status_code=503, detail="Index not ready")

        requested_fields = None
        if fields:
            requested_fields = tuple(
                field.strip() for field in fields.split(",") if field.strip()
            )

        with app_state._lock:
            try:
                result = fetch_anime_metadata(
                    con, anilist_id, fields=requested_fields
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="AniList anime not found")
        return result

    @app.get("/metrics")
    def metrics() -> Response:
        con = app_state.con
        if con is None:
            raise HTTPException(status_code=503, detail="Index not ready")
        from ja_media_services.anilist_search.metrics import render_metrics

        with app_state._lock:
            rows = get_row_count(con)
        return Response(
            render_metrics(app_state.refresh_status, rows),
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/health")
    def health() -> dict:
        con = app_state.con
        if con is None:
            raise HTTPException(status_code=503, detail="Index not ready")
        with app_state._lock:
            rows = get_row_count(con)
        refresh = app_state.refresh_status.as_dict(
            stale_after_seconds=max(app_state.update_interval_seconds * 3, 1)
        )
        status = "degraded" if refresh["stale"] or refresh["consecutive_failures"] else "ok"
        if status == "degraded":
            logger.warning(
                "AniList search health is degraded (rows=%d refresh=%s)",
                rows,
                refresh,
            )
        return {"status": status, "rows": rows, "refresh": refresh}

    return app


app = create_app()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Run the AniList search service")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = _get_settings()
    uvicorn.run(
        "ja_media_services.anilist_search.app:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
    )
