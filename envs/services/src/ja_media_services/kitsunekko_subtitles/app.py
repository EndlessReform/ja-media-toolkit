from __future__ import annotations

import argparse
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

from ja_media_services.kitsunekko_subtitles.db import (
    connect_readonly,
    fetch_metadata,
)
from ja_media_services.kitsunekko_subtitles.metrics import render_metrics
from ja_media_services.kitsunekko_subtitles.settings import KitsunekkoSubtitlesSettings


logger = logging.getLogger(__name__)


def get_settings() -> KitsunekkoSubtitlesSettings:
    return KitsunekkoSubtitlesSettings()


@lru_cache(maxsize=1)
def get_connection(db_path: str):
    return connect_readonly(Path(db_path))


def build_llms_txt(settings: KitsunekkoSubtitlesSettings) -> str:
    """Build the public LLM orientation document for this LAN service."""

    return "\n".join(
        [
            "# ja-media-toolkit Kitsunekko subtitle service",
            "",
            "> LAN-only FastAPI service for subtitle inventory and retrieval over a local Kitsunekko mirror.",
            "",
            "This service currently exposes the v0 ceremony: a generated SQLite database, health, stats, metrics, and source-version metadata loaded from the anime crosswalk database. Kitsunekko mirror pulling and subtitle indexing are intentionally not enabled yet.",
            "",
            "## API",
            "",
            "- [Health](/healthz): Service health and DB availability.",
            "- [Stats](/stats): Source commits, row counts, and schema version.",
            "- [Metrics](/metrics): Prometheus-format DB gauges.",
            "- [Series by AniList](/series/anilist/1): Placeholder series summary contract.",
            "- [Files by AniList](/series/anilist/1/files): Placeholder file-list contract.",
            "",
            "No public internet exposure or authentication is expected in the trusted LAN deployment.",
            "",
            f"Runtime DB: `{settings.db_path}`",
            f"Crosswalk DB: `{settings.crosswalk_db_path}`",
            "",
        ]
    )


def create_app(settings: KitsunekkoSubtitlesSettings | None = None) -> FastAPI:
    active_settings = settings or KitsunekkoSubtitlesSettings()
    app = FastAPI(title="ja-media Kitsunekko subtitles")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        connection = get_connection(str(active_settings.db_path))
        metadata = fetch_metadata(connection)
        return {
            "ok": True,
            "db_path": str(active_settings.db_path),
            "mirror_commit": metadata.get("mirror_commit"),
            "crosswalk_source_commit": metadata.get("crosswalk_source_commit"),
            "ingest_phase": metadata.get("ingest_phase"),
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

    @app.get("/series/anilist/{anilist_id}")
    def series_by_anilist(anilist_id: int) -> dict[str, Any]:
        return {
            "anilist_id": anilist_id,
            "exists": False,
            "file_count": 0,
            "files_url": f"/series/anilist/{anilist_id}/files",
            "ingest_phase": "crosswalk-only",
        }

    @app.get("/series/anilist/{anilist_id}/files")
    def files_by_anilist(anilist_id: int) -> dict[str, Any]:
        return {"anilist_id": anilist_id, "count": 0, "files": []}

    logger.info(
        "kitsunekko-subtitles configured with DB %s and crosswalk DB %s",
        active_settings.db_path,
        active_settings.crosswalk_db_path,
    )
    return app


app = create_app()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Kitsunekko subtitle API")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = KitsunekkoSubtitlesSettings()
    uvicorn.run(
        "ja_media_services.kitsunekko_subtitles.app:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
    )
