"""FastAPI surface for indexed derived anime audio."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse

from ja_media_services.anime_audio.db import (
    fetch_artifact,
    fetch_artifacts,
    fetch_series,
    initialize,
    resolve_content_path,
    stats,
)
from ja_media_services.anime_audio.index import reconcile
from ja_media_services.anime_audio.metrics import render_metrics
from ja_media_services.anime_audio.settings import AnimeAudioSettings

logger = logging.getLogger(__name__)


def create_app(settings: AnimeAudioSettings | None = None) -> FastAPI:
    """Build an app with one process-local SQLite connection and scan lock."""

    active = settings or AnimeAudioSettings()
    connection = initialize(active.db_path)
    lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        connection.close()

    app = FastAPI(
        title="ja-media indexed anime audio",
        root_path=active.root_path,
        lifespan=lifespan,
    )

    try:
        with lock:
            reconcile(connection, active.library_root)
    except FileNotFoundError:
        logger.warning("Anime audio library root is unavailable at startup")

    @app.get("/series/{anilist_id}")
    def series_endpoint(anilist_id: int) -> dict[str, Any]:
        with lock:
            result = fetch_series(connection, anilist_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Anime audio series not found")
        return result

    @app.get("/series/{anilist_id}/episodes")
    def episodes_endpoint(anilist_id: int) -> list[dict[str, Any]]:
        with lock:
            if fetch_series(connection, anilist_id) is None:
                raise HTTPException(status_code=404, detail="Anime audio series not found")
            artifacts = fetch_artifacts(connection, anilist_id)
        episodes: dict[str, list[dict[str, Any]]] = {}
        for artifact in artifacts:
            episodes.setdefault(str(artifact["episode_key"]), []).append(
                _public_artifact(artifact)
            )
        return [
            {"anilist_id": anilist_id, "episode_key": key, "artifacts": values}
            for key, values in episodes.items()
        ]

    @app.get("/series/{anilist_id}/episodes/{episode_key}")
    def episode_endpoint(anilist_id: int, episode_key: str) -> dict[str, Any]:
        episodes = episodes_endpoint(anilist_id)
        for episode in episodes:
            if episode["episode_key"] == episode_key:
                return episode
        raise HTTPException(status_code=404, detail="Anime audio episode not found")

    @app.get(
        "/series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}"
    )
    def artifact_endpoint(
        anilist_id: int, episode_key: str, profile: str
    ) -> dict[str, Any]:
        return _get_public_artifact(connection, lock, anilist_id, episode_key, profile)

    @app.get(
        "/series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}/content"
    )
    def content_endpoint(anilist_id: int, episode_key: str, profile: str) -> FileResponse:
        with lock:
            artifact = fetch_artifact(connection, anilist_id, episode_key, profile)
            if artifact is None:
                raise HTTPException(status_code=404, detail="Anime audio artifact not found")
            try:
                path = resolve_content_path(connection, active.library_root, artifact)
            except (FileNotFoundError, ValueError) as error:
                raise HTTPException(status_code=503, detail="Indexed artifact is unavailable") from error
        if not path.is_file():
            raise HTTPException(status_code=503, detail="Indexed artifact is unavailable")
        return FileResponse(path, media_type="audio/mp4", filename=path.name)

    @app.post("/reconcile")
    def reconcile_endpoint() -> dict[str, Any]:
        try:
            with lock:
                return reconcile(connection, active.library_root).__dict__
        except FileNotFoundError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/healthz")
    def health_endpoint() -> dict[str, Any]:
        with lock:
            state = stats(connection)
        if not state["ready"]:
            raise HTTPException(status_code=503, detail={"status": "unavailable", **state})
        return {
            "status": "degraded" if state["error_count"] or state["last_failure_code"] else "ok",
            **state,
        }

    @app.get("/metrics", response_class=Response)
    def metrics_endpoint() -> Response:
        with lock:
            body = render_metrics(stats(connection))
        return Response(body, media_type="text/plain; version=0.0.4")

    return app


def _get_public_artifact(
    connection: sqlite3.Connection,
    lock: threading.RLock,
    anilist_id: int,
    episode_key: str,
    profile: str,
) -> dict[str, Any]:
    with lock:
        artifact = fetch_artifact(connection, anilist_id, episode_key, profile)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Anime audio artifact not found")
    return _public_artifact(artifact)


def _public_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    anilist_id = artifact["anilist_id"]
    episode_key = artifact["episode_key"]
    profile = artifact["profile"]
    return {
        **artifact,
        "content_url": (
            f"/series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}/content"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the indexed anime-audio service")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    settings = AnimeAudioSettings()
    uvicorn.run(
        "ja_media_services.anime_audio.app:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level.lower(),
    )
