from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import duckdb
from aiolimiter import AsyncLimiter
import uvicorn
from fastapi import FastAPI, HTTPException, Query

from ja_media_services.anilist_search.db import (
    get_row_count,
    open_db,
    rebuild_from_cached_csv,
    resolve_formats,
    search,
)
from ja_media_services.anilist_search.anilist_api import AniListGraphQLClient
from ja_media_services.anilist_search.dataset import ensure_dataset
from ja_media_services.anilist_search.exact_fallback import (
    ExactFallbackNotFound,
    ExactFallbackUnavailable,
    resolve_exact_fallback,
)
from ja_media_services.anilist_search.fallback_cache import FallbackTtlPolicy
from ja_media_services.anilist_search.metadata import (
    anime_metadata_exists,
    fetch_anime_metadata,
)
from ja_media_services.anilist_search.refresh import RefreshStatus, background_refresh
from ja_media_services.anilist_search.singleflight import ExactIdSingleFlight

logger = logging.getLogger("ja_media_services.anilist_search")


class AppState:
    """Holds the persistent DuckDB connection and data paths."""
    con: duckdb.DuckDBPyConnection | None = None
    csv_path: Path | None = None
    db_path: Path | None = None
    data_dir: Path | None = None
    update_interval_seconds: int = 3600
    anilist_limiter: AsyncLimiter | None = None
    anilist_client: AniListGraphQLClient | None = None
    fallback_ttl_policy: FallbackTtlPolicy | None = None
    exact_id_singleflight = ExactIdSingleFlight()
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
        row_count, con = rebuild_from_cached_csv(csv_path, db_path, con)

    app_state.con = con
    app_state.csv_path = csv_path
    app_state.db_path = db_path
    app_state.data_dir = data_dir
    app_state.update_interval_seconds = settings.update_interval_seconds
    app_state.anilist_limiter = AsyncLimiter(
        settings.anilist_rate_limit_calls,
        settings.anilist_rate_limit_period_seconds,
    )
    app_state.anilist_client = AniListGraphQLClient(
        endpoint=settings.anilist_endpoint,
        timeout_seconds=settings.anilist_timeout_seconds,
        limiter=app_state.anilist_limiter,
    )
    app_state.fallback_ttl_policy = FallbackTtlPolicy(
        airing_seconds=settings.fallback_airing_ttl_seconds,
        finished_seconds=settings.fallback_finished_ttl_seconds,
        negative_seconds=settings.fallback_negative_ttl_seconds,
    )
    app_state.exact_id_singleflight = ExactIdSingleFlight()
    app_state.refresh_status.last_attempt_unix = time.time()
    app_state.refresh_status.last_success_unix = app_state.refresh_status.last_attempt_unix
    app_state.refresh_status.last_failure_unix = None
    app_state.refresh_status.last_failure = None
    app_state.refresh_status.consecutive_failures = 0
    app_state.refresh_status.last_index_rows = row_count

    t = threading.Thread(
        target=background_refresh,
        args=(
            app_state,
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

    with app_state._lock:
        active_connection = app_state.con
        active_anilist_client = app_state.anilist_client
        app_state.con = None
        app_state.anilist_limiter = None
        app_state.anilist_client = None
        app_state.fallback_ttl_policy = None
        app_state.exact_id_singleflight = ExactIdSingleFlight()
        if active_connection is not None:
            active_connection.close()
    if active_anilist_client is not None:
        await active_anilist_client.aclose()


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
    async def anime_detail_endpoint(
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
            local_exists = anime_metadata_exists(con, anilist_id)
            if local_exists:
                try:
                    result = fetch_anime_metadata(
                        con, anilist_id, fields=requested_fields
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                if result is not None:
                    return result

        anilist_client = app_state.anilist_client
        ttl_policy = app_state.fallback_ttl_policy
        if anilist_client is None or ttl_policy is None:
            raise HTTPException(status_code=503, detail="AniList fallback not ready")

        try:
            return await resolve_exact_fallback(
                anilist_id=anilist_id,
                fields=requested_fields,
                con=con,
                db_lock=app_state._lock,
                client=anilist_client,
                ttl_policy=ttl_policy,
                singleflight=app_state.exact_id_singleflight,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ExactFallbackNotFound as exc:
            raise HTTPException(status_code=404, detail="AniList anime not found")
        except ExactFallbackUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/health", include_in_schema=False)
    @app.get("/healthz")
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
