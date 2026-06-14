from __future__ import annotations

import argparse
import gzip
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from ja_media_core.crosswalk import normalize_media_kind, normalize_source

from ja_media_services.anime_crosswalk.db import (
    connect_readonly,
    fetch_lookup,
    fetch_metadata,
)
from ja_media_services.anime_crosswalk.metrics import render_metrics
from ja_media_services.anime_crosswalk.settings import AnimeCrosswalkSettings


VALID_SOURCES = {
    "anidb",
    "mal",
    "anilist",
    "kitsu",
    "tvdb",
    "tmdb",
    "imdb",
    "anime-planet",
    "anisearch",
    "animenewsnetwork",
    "livechart",
    "simkl",
}
VALID_MEDIA_KINDS = {"tv", "movie"}

logger = logging.getLogger(__name__)


def get_settings() -> AnimeCrosswalkSettings:
    return AnimeCrosswalkSettings()


@lru_cache(maxsize=1)
def get_connection(db_path: str):
    return connect_readonly(Path(db_path))


def response_payload(
    *,
    source: str,
    external_id: str,
    media_kind: str | None,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the stable lookup response shape."""

    return {
        "source": source,
        "id": external_id,
        "media_kind": media_kind,
        "count": len(results),
        "results": results,
    }


def lookup(
    *,
    source: str,
    external_id: str,
    media_kind: str | None,
    settings: AnimeCrosswalkSettings,
) -> dict[str, Any]:
    normalized_source = normalize_source(source)
    normalized_kind = normalize_media_kind(media_kind)
    if normalized_source not in VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"Invalid source: {source}")
    if normalized_kind is not None and normalized_kind not in VALID_MEDIA_KINDS:
        raise HTTPException(status_code=400, detail=f"Invalid media kind: {media_kind}")

    connection = get_connection(str(settings.db_path))
    results = fetch_lookup(
        connection,
        source=normalized_source,
        external_id=str(external_id),
        media_kind=normalized_kind,
    )
    return response_payload(
        source=normalized_source,
        external_id=str(external_id),
        media_kind=normalized_kind,
        results=results,
    )


def build_llms_txt(settings: AnimeCrosswalkSettings) -> str:
    """Build the public LLM orientation document for this LAN service."""

    readme_path = settings.repo_root / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    return "\n".join(
        [
            "# ja-media-toolkit anime crosswalk service",
            "",
            "> LAN-only FastAPI service for resolving anime metadata IDs across "
            "Fribb/anime-lists sources such as TVDB, MAL, AniDB, and TMDB.",
            "",
            "This file follows Jeremy Howard's `/llms.txt` proposal: concise "
            "Markdown at a predictable path for non-human consumers at "
            "inference time. The API returns JSON and never requires auth on "
            "the trusted LAN deployment.",
            "",
            "## API",
            "",
            "- [Health](/healthz): Service health and DB availability.",
            "- [Stats](/stats): Source commit, row counts, lookup counts, and schema version.",
            "- [Metrics](/metrics): Prometheus-format DB gauges.",
            "- [Source JSON](/data/anime-list-full.json): Original anime-list-full.json; send `Accept-Encoding: gzip` for compressed transfer.",
            "- [Resolve broad](/resolve/tvdb/79099): Resolve `/{source}/{id}` with all matches.",
            "- [Resolve by kind](/resolve/tmdb/tv/8864): Resolve `/{source}/{media_kind}/{id}` for TV/movie-specific sources.",
            "- [TVDB](/tvdb/79099): Shortcut for broad TVDB lookup.",
            "- [TVDB series](/tvdb/series/79099): Shortcut for TVDB series-like lookup.",
            "- [TVDB movie](/tvdb/movie/79099): Shortcut for TVDB movie lookup.",
            "- [MAL](/mal/3269): Shortcut for MyAnimeList lookup.",
            "- [AniDB](/anidb/5459): Shortcut for AniDB lookup.",
            "- [TMDB TV](/tmdb/tv/8864): Shortcut for TMDB TV lookup.",
            "- [TMDB movie](/tmdb/movie/128): Shortcut for TMDB movie lookup.",
            "",
            "## Response Contract",
            "",
            "Lookup responses always allow multiple results:",
            "",
            "```json",
            '{"source":"tvdb","id":"79099","media_kind":null,"count":1,"results":[{}]}',
            "```",
            "",
            "No match is a `200` with `count: 0` and an empty `results` array. "
            "Invalid sources or media kinds are `400` responses.",
            "",
            "## Optional",
            "",
            "- [Project README](#project-readme): The checked-out repository README, reproduced below for broader project context.",
            "",
            "## Project README",
            "",
            readme.rstrip(),
            "",
        ]
    )


def create_app(settings: AnimeCrosswalkSettings | None = None) -> FastAPI:
    active_settings = settings or AnimeCrosswalkSettings()
    app = FastAPI(title="ja-media anime crosswalk")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        connection = get_connection(str(active_settings.db_path))
        metadata = fetch_metadata(connection)
        return {
            "ok": True,
            "db_path": str(active_settings.db_path),
            "source_commit": metadata.get("source_commit"),
        }

    @app.get("/stats")
    def stats() -> dict[str, str]:
        return fetch_metadata(get_connection(str(active_settings.db_path)))

    @app.get("/metrics")
    def metrics() -> Response:
        metadata = fetch_metadata(get_connection(str(active_settings.db_path)))
        return Response(render_metrics(metadata), media_type="text/plain; version=0.0.4")

    @app.get("/llms.txt")
    def llms_txt() -> PlainTextResponse:
        return PlainTextResponse(build_llms_txt(active_settings), media_type="text/markdown")

    @app.get("/data/anime-list-full.json")
    def source_json(request: Request) -> Response:
        source_path = active_settings.source_json_path
        if source_path is None or not source_path.exists():
            raise HTTPException(status_code=404, detail="Source JSON path is not configured")
        if "gzip" in request.headers.get("accept-encoding", "").lower():
            compressed = gzip.compress(source_path.read_bytes(), compresslevel=6)
            return Response(
                compressed,
                media_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Vary": "Accept-Encoding",
                    "Content-Disposition": 'inline; filename="anime-list-full.json"',
                },
            )
        return FileResponse(source_path, media_type="application/json")

    @app.get("/resolve/{source}/{external_id}")
    def resolve_broad(
        source: str,
        external_id: str,
    ) -> dict[str, Any]:
        return lookup(
            source=source,
            external_id=external_id,
            media_kind=None,
            settings=active_settings,
        )

    @app.get("/resolve/{source}/{media_kind}/{external_id}")
    def resolve_kind(
        source: str,
        media_kind: str,
        external_id: str,
    ) -> dict[str, Any]:
        return lookup(
            source=source,
            external_id=external_id,
            media_kind=media_kind,
            settings=active_settings,
        )

    @app.get("/tvdb/{external_id}")
    def tvdb(external_id: str) -> dict[str, Any]:
        return lookup(source="tvdb", external_id=external_id, media_kind=None, settings=active_settings)

    @app.get("/tvdb/series/{external_id}")
    def tvdb_series(external_id: str) -> dict[str, Any]:
        return lookup(source="tvdb", external_id=external_id, media_kind="tv", settings=active_settings)

    @app.get("/tvdb/movie/{external_id}")
    def tvdb_movie(external_id: str) -> dict[str, Any]:
        return lookup(source="tvdb", external_id=external_id, media_kind="movie", settings=active_settings)

    @app.get("/mal/{external_id}")
    def mal(external_id: str) -> dict[str, Any]:
        return lookup(source="mal", external_id=external_id, media_kind=None, settings=active_settings)

    @app.get("/anidb/{external_id}")
    def anidb(external_id: str) -> dict[str, Any]:
        return lookup(source="anidb", external_id=external_id, media_kind=None, settings=active_settings)

    @app.get("/tmdb/tv/{external_id}")
    def tmdb_tv(external_id: str) -> dict[str, Any]:
        return lookup(source="tmdb", external_id=external_id, media_kind="tv", settings=active_settings)

    @app.get("/tmdb/movie/{external_id}")
    def tmdb_movie(external_id: str) -> dict[str, Any]:
        return lookup(source="tmdb", external_id=external_id, media_kind="movie", settings=active_settings)

    logger.info("anime-crosswalk configured with DB %s", active_settings.db_path)
    return app


app = create_app()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the anime crosswalk API")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = AnimeCrosswalkSettings()
    uvicorn.run(
        "ja_media_services.anime_crosswalk.app:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
    )
