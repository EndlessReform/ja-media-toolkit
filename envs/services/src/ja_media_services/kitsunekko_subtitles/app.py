from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import sqlite3
import tarfile
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from ja_media_core.crosswalk import normalize_media_kind

from ja_media_services.kitsunekko_subtitles.db import (
    connect_readonly,
    fetch_file_by_subtitle_id,
    fetch_files_by_anilist,
    fetch_files_by_anilist_ids_with_prefix,
    fetch_files_by_anilist_with_prefix,
    fetch_files_by_anilist_ids,
    fetch_files_by_filename,
    fetch_files_by_repo_path,
    fetch_metadata,
)
from ja_media_services.kitsunekko_subtitles.episode import filter_files_by_runtime_episode
from ja_media_services.kitsunekko_subtitles.metrics import render_metrics
from ja_media_services.kitsunekko_subtitles.settings import KitsunekkoSubtitlesSettings


logger = logging.getLogger(__name__)

ARCHIVE_MEDIA_TYPE = "application/x-tar"
GZIP_MEDIA_TYPE = "application/gzip"
SUBTITLE_MEDIA_TYPES = {
    ".ass": "text/plain; charset=utf-8",
    ".srt": "text/plain; charset=utf-8",
    ".ssa": "text/plain; charset=utf-8",
    ".vtt": "text/vtt; charset=utf-8",
}


def get_settings() -> KitsunekkoSubtitlesSettings:
    return KitsunekkoSubtitlesSettings()


@lru_cache(maxsize=1)
def get_connection(db_path: str):
    return connect_readonly(Path(db_path))


@lru_cache(maxsize=1)
def get_crosswalk_connection(db_path: str):
    uri = f"file:{Path(db_path)}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def resolve_anilist_ids(
    *,
    crosswalk_db_path: Path,
    source: str,
    external_id: str,
    media_kind: str | None,
) -> list[int]:
    """Resolve a source ID to AniList IDs through the mounted crosswalk DB."""

    connection = get_crosswalk_connection(str(crosswalk_db_path))
    rows = connection.execute(
        """
        SELECT anime.payload_json
        FROM lookup
        JOIN anime USING (row_id)
        WHERE lookup.source = ?
          AND lookup.external_id = ?
          AND (? IS NULL OR lookup.media_kind = ?)
        ORDER BY anime.row_id
        """,
        (source, external_id, media_kind, media_kind),
    ).fetchall()
    anilist_ids: set[int] = set()
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        anilist_id = coerce_int(payload.get("anilist_id"))
        if anilist_id is not None:
            anilist_ids.add(anilist_id)
    return sorted(anilist_ids)


def wants_gzip(*, compression: str, accept_encoding: str | None) -> bool:
    """Return whether this response should be gzip encoded.

    `compression=gzip` is useful for curl and tests. `compression=auto` follows
    `Accept-Encoding`, which lets ordinary HTTP clients negotiate compression
    without learning another service-specific knob.
    """

    normalized = compression.lower()
    if normalized == "gzip":
        return True
    if normalized in {"none", "identity"}:
        return False
    if normalized != "auto":
        raise HTTPException(status_code=400, detail="compression must be auto, gzip, or none")
    return "gzip" in (accept_encoding or "").lower()


def is_uuid(value: str) -> bool:
    """Return whether a string is parseable as a UUID."""

    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def resolve_file_ref(connection: sqlite3.Connection, file_ref: str) -> dict[str, Any]:
    """Resolve a public file reference to exactly one subtitle row."""

    if is_uuid(file_ref):
        file = fetch_file_by_subtitle_id(connection, file_ref)
        if file is None:
            raise HTTPException(status_code=404, detail=f"Unknown subtitle_id: {file_ref}")
        return file

    repo_matches = fetch_files_by_repo_path(connection, file_ref)
    if len(repo_matches) == 1:
        return repo_matches[0]

    filename_matches = fetch_files_by_filename(connection, file_ref)
    if len(filename_matches) == 1:
        return filename_matches[0]
    if len(repo_matches) + len(filename_matches) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "File reference is ambiguous; use subtitle_id or exact repo_path.",
                "ref": file_ref,
                "matches": repo_matches + filename_matches,
            },
        )
    raise HTTPException(status_code=404, detail=f"Unknown subtitle file reference: {file_ref}")


def mirror_file_path(mirror_dir: Path, file: dict[str, Any]) -> Path:
    """Return the absolute mirror path for an indexed subtitle row."""

    mirror_root = mirror_dir.resolve()
    file_path = (mirror_root / str(file["repo_path"])).resolve()
    try:
        file_path.relative_to(mirror_root)
    except ValueError as error:
        raise HTTPException(status_code=500, detail="Indexed repo_path escaped mirror root") from error
    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"message": "Indexed subtitle file is missing from the mirror.", "file": file},
        )
    return file_path


def content_disposition(filename: str) -> str:
    """Return a conservative attachment header value for generated responses."""

    safe = filename.replace("\\", "_").replace("/", "_").replace('"', "'")
    return f'attachment; filename="{safe}"'


def single_file_response(
    *,
    file: dict[str, Any],
    file_path: Path,
    gzip_enabled: bool,
) -> Response:
    """Read and render one subtitle file."""

    body = file_path.read_bytes()
    headers = {"Content-Disposition": content_disposition(str(file["filename"]))}
    media_type = SUBTITLE_MEDIA_TYPES.get(str(file["extension"]).lower(), "application/octet-stream")
    if gzip_enabled:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
    return Response(body, media_type=media_type, headers=headers)


def archive_response(
    *,
    files: list[dict[str, Any]],
    mirror_dir: Path,
    archive_name: str,
    gzip_enabled: bool,
) -> Response:
    """Build a tar or tar.gz archive containing indexed subtitle files."""

    buffer = io.BytesIO()
    mode = "w:gz" if gzip_enabled else "w"
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for file in files:
            file_path = mirror_file_path(mirror_dir, file)
            archive.add(file_path, arcname=str(file["repo_path"]), recursive=False)

    headers = {"Content-Disposition": content_disposition(archive_name)}
    if gzip_enabled:
        return Response(buffer.getvalue(), media_type=GZIP_MEDIA_TYPE, headers=headers)
    return Response(buffer.getvalue(), media_type=ARCHIVE_MEDIA_TYPE, headers=headers)


def build_llms_txt(settings: KitsunekkoSubtitlesSettings) -> str:
    """Build the public LLM orientation document for this LAN service."""

    return "\n".join(
        [
            "# ja-media-toolkit Kitsunekko subtitle service",
            "",
            "> LAN-only FastAPI service for subtitle inventory and retrieval over a local Kitsunekko mirror.",
            "",
            "This service exposes a generated SQLite subtitle index over the local Kitsunekko mirror. The index is rebuilt from the mirror and the mounted anime crosswalk database, then served read-only.",
            "",
            "## API",
            "",
            "- [Health](/healthz): Service health and DB availability.",
            "- [Stats](/stats): Source commits, row counts, and schema version.",
            "- [Metrics](/metrics): Prometheus-format DB gauges.",
            "- [Series by AniList](/series/anilist/395): Series summary by AniList ID.",
            "- [Files by AniList](/series/anilist/395/files): Subtitle file list by AniList ID.",
            "- [Episode files by AniList](/series/anilist/395/episodes/16/files): Subtitle file list for one local episode number, parsed from filenames at request time.",
            "- [Files by TVDB](/series/tvdb/79099/files): Subtitle file list by broad TVDB ID.",
            "- [Files by TVDB kind](/series/tvdb/tv/79099/files): Subtitle file list by TVDB media kind.",
            "- [Episode files by TVDB](/series/tvdb/79099/episodes/16/files): Subtitle file list for one local episode number after runtime TVDB-to-AniList resolution.",
            "- [Episode files by TVDB kind](/series/tvdb/tv/79099/episodes/16/files): Kind-scoped TVDB episode subtitle file list.",
            "- [File metadata](/files/{subtitle_id-or-name}): One indexed subtitle row.",
            "- [File content](/files/{subtitle_id-or-name}/content): One subtitle file, optionally gzip-compressed.",
            "- [Multiple file content](/files/content?ref={subtitle_id}&ref={repo_path}): Tar or tar.gz archive for one or more file refs.",
            "- [Series content](/series/anilist/395/content): Tar or tar.gz archive for a series, optionally filtered by repo_path prefix.",
            "- [Episode content by AniList](/series/anilist/395/episodes/16/content): Tar or tar.gz archive for one episode, optionally filtered by repo_path prefix.",
            "- [Episode content by TVDB](/series/tvdb/79099/episodes/16/content): Tar or tar.gz archive for one TVDB-resolved episode.",
            "",
            "No public internet exposure or authentication is expected in the trusted LAN deployment.",
            "Episode endpoints use `parse-torrent-title`/`PTN` against filenames at request time; episode numbers are local episode numbers, not globally materialized IDs.",
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
        files = fetch_files_by_anilist(get_connection(str(active_settings.db_path)), anilist_id)
        return {
            "anilist_id": anilist_id,
            "exists": bool(files),
            "file_count": len(files),
            "files_url": f"/series/anilist/{anilist_id}/files",
            "extensions": sorted({str(file["extension"]) for file in files}),
        }

    @app.get("/series/anilist/{anilist_id}/files")
    def files_by_anilist(anilist_id: int) -> dict[str, Any]:
        files = fetch_files_by_anilist(get_connection(str(active_settings.db_path)), anilist_id)
        return {"anilist_id": anilist_id, "count": len(files), "files": files}

    @app.get("/series/anilist/{anilist_id}/episodes/{episode_number}/files")
    def files_by_anilist_episode(anilist_id: int, episode_number: int) -> dict[str, Any]:
        files = filter_files_by_runtime_episode(
            fetch_files_by_anilist(get_connection(str(active_settings.db_path)), anilist_id),
            episode_number,
        )
        return {
            "anilist_id": anilist_id,
            "episode_number": episode_number,
            "count": len(files),
            "files": files,
        }

    @app.get("/series/anilist/{anilist_id}/episodes/{episode_number}/content")
    def content_by_anilist_episode(
        anilist_id: int,
        episode_number: int,
        prefix: str | None = None,
        compression: str = "auto",
        accept_encoding: str | None = Header(default=None),
    ) -> Response:
        files = filter_files_by_runtime_episode(
            fetch_files_by_anilist(get_connection(str(active_settings.db_path)), anilist_id),
            episode_number,
            prefix=prefix,
        )
        if not files:
            raise HTTPException(status_code=404, detail="No subtitle files matched this episode request")
        gzip_enabled = wants_gzip(compression=compression, accept_encoding=accept_encoding)
        suffix = ".tar.gz" if gzip_enabled else ".tar"
        prefix_label = f"-{prefix.strip('/').replace('/', '_')}" if prefix else ""
        return archive_response(
            files=files,
            mirror_dir=active_settings.mirror_dir,
            archive_name=f"anilist-{anilist_id}-episode-{episode_number}{prefix_label}{suffix}",
            gzip_enabled=gzip_enabled,
        )

    @app.get("/series/anilist/{anilist_id}/content")
    def content_by_anilist(
        anilist_id: int,
        prefix: str | None = None,
        compression: str = "auto",
        accept_encoding: str | None = Header(default=None),
    ) -> Response:
        files = fetch_files_by_anilist_with_prefix(
            get_connection(str(active_settings.db_path)),
            anilist_id,
            prefix,
        )
        if not files:
            raise HTTPException(status_code=404, detail="No subtitle files matched this series request")
        suffix = ".tar.gz" if wants_gzip(compression=compression, accept_encoding=accept_encoding) else ".tar"
        prefix_label = f"-{prefix.strip('/').replace('/', '_')}" if prefix else ""
        return archive_response(
            files=files,
            mirror_dir=active_settings.mirror_dir,
            archive_name=f"anilist-{anilist_id}{prefix_label}{suffix}",
            gzip_enabled=suffix.endswith(".gz"),
        )

    @app.get("/series/tvdb/{tvdb_id}")
    def series_by_tvdb(tvdb_id: str) -> dict[str, Any]:
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=None,
        )
        files = fetch_files_by_anilist_ids(
            get_connection(str(active_settings.db_path)),
            anilist_ids,
        )
        return {
            "source": "tvdb",
            "id": tvdb_id,
            "media_kind": None,
            "exists": bool(files),
            "file_count": len(files),
            "anilist_ids": anilist_ids,
            "files_url": f"/series/tvdb/{tvdb_id}/files",
        }

    @app.get("/series/tvdb/{tvdb_id}/files")
    def files_by_tvdb(tvdb_id: str) -> dict[str, Any]:
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=None,
        )
        files = fetch_files_by_anilist_ids(
            get_connection(str(active_settings.db_path)),
            anilist_ids,
        )
        return {
            "source": "tvdb",
            "id": tvdb_id,
            "media_kind": None,
            "anilist_ids": anilist_ids,
            "count": len(files),
            "files": files,
        }

    @app.get("/series/tvdb/{tvdb_id}/episodes/{episode_number}/files")
    def files_by_tvdb_episode(tvdb_id: str, episode_number: int) -> dict[str, Any]:
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=None,
        )
        files = filter_files_by_runtime_episode(
            fetch_files_by_anilist_ids(
                get_connection(str(active_settings.db_path)),
                anilist_ids,
            ),
            episode_number,
        )
        return {
            "source": "tvdb",
            "id": tvdb_id,
            "media_kind": None,
            "anilist_ids": anilist_ids,
            "episode_number": episode_number,
            "count": len(files),
            "files": files,
        }

    @app.get("/series/tvdb/{tvdb_id}/episodes/{episode_number}/content")
    def content_by_tvdb_episode(
        tvdb_id: str,
        episode_number: int,
        prefix: str | None = None,
        compression: str = "auto",
        accept_encoding: str | None = Header(default=None),
    ) -> Response:
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=None,
        )
        files = filter_files_by_runtime_episode(
            fetch_files_by_anilist_ids(
                get_connection(str(active_settings.db_path)),
                anilist_ids,
            ),
            episode_number,
            prefix=prefix,
        )
        if not files:
            raise HTTPException(status_code=404, detail="No subtitle files matched this episode request")
        gzip_enabled = wants_gzip(compression=compression, accept_encoding=accept_encoding)
        suffix = ".tar.gz" if gzip_enabled else ".tar"
        prefix_label = f"-{prefix.strip('/').replace('/', '_')}" if prefix else ""
        return archive_response(
            files=files,
            mirror_dir=active_settings.mirror_dir,
            archive_name=f"tvdb-{tvdb_id}-episode-{episode_number}{prefix_label}{suffix}",
            gzip_enabled=gzip_enabled,
        )

    @app.get("/series/tvdb/{tvdb_id}/content")
    def content_by_tvdb(
        tvdb_id: str,
        prefix: str | None = None,
        compression: str = "auto",
        accept_encoding: str | None = Header(default=None),
    ) -> Response:
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=None,
        )
        files = fetch_files_by_anilist_ids_with_prefix(
            get_connection(str(active_settings.db_path)),
            anilist_ids,
            prefix,
        )
        if not files:
            raise HTTPException(status_code=404, detail="No subtitle files matched this series request")
        gzip_enabled = wants_gzip(compression=compression, accept_encoding=accept_encoding)
        suffix = ".tar.gz" if gzip_enabled else ".tar"
        prefix_label = f"-{prefix.strip('/').replace('/', '_')}" if prefix else ""
        return archive_response(
            files=files,
            mirror_dir=active_settings.mirror_dir,
            archive_name=f"tvdb-{tvdb_id}{prefix_label}{suffix}",
            gzip_enabled=gzip_enabled,
        )

    @app.get("/series/tvdb/{media_kind}/{tvdb_id}")
    def series_by_tvdb_kind(media_kind: str, tvdb_id: str) -> dict[str, Any]:
        normalized_kind = normalize_media_kind(media_kind)
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=normalized_kind,
        )
        files = fetch_files_by_anilist_ids(
            get_connection(str(active_settings.db_path)),
            anilist_ids,
        )
        return {
            "source": "tvdb",
            "id": tvdb_id,
            "media_kind": normalized_kind,
            "exists": bool(files),
            "file_count": len(files),
            "anilist_ids": anilist_ids,
            "files_url": f"/series/tvdb/{normalized_kind}/{tvdb_id}/files",
        }

    @app.get("/series/tvdb/{media_kind}/{tvdb_id}/files")
    def files_by_tvdb_kind(media_kind: str, tvdb_id: str) -> dict[str, Any]:
        normalized_kind = normalize_media_kind(media_kind)
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=normalized_kind,
        )
        files = fetch_files_by_anilist_ids(
            get_connection(str(active_settings.db_path)),
            anilist_ids,
        )
        return {
            "source": "tvdb",
            "id": tvdb_id,
            "media_kind": normalized_kind,
            "anilist_ids": anilist_ids,
            "count": len(files),
            "files": files,
        }

    @app.get("/series/tvdb/{media_kind}/{tvdb_id}/episodes/{episode_number}/files")
    def files_by_tvdb_kind_episode(
        media_kind: str,
        tvdb_id: str,
        episode_number: int,
    ) -> dict[str, Any]:
        normalized_kind = normalize_media_kind(media_kind)
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=normalized_kind,
        )
        files = filter_files_by_runtime_episode(
            fetch_files_by_anilist_ids(
                get_connection(str(active_settings.db_path)),
                anilist_ids,
            ),
            episode_number,
        )
        return {
            "source": "tvdb",
            "id": tvdb_id,
            "media_kind": normalized_kind,
            "anilist_ids": anilist_ids,
            "episode_number": episode_number,
            "count": len(files),
            "files": files,
        }

    @app.get("/series/tvdb/{media_kind}/{tvdb_id}/episodes/{episode_number}/content")
    def content_by_tvdb_kind_episode(
        media_kind: str,
        tvdb_id: str,
        episode_number: int,
        prefix: str | None = None,
        compression: str = "auto",
        accept_encoding: str | None = Header(default=None),
    ) -> Response:
        normalized_kind = normalize_media_kind(media_kind)
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=normalized_kind,
        )
        files = filter_files_by_runtime_episode(
            fetch_files_by_anilist_ids(
                get_connection(str(active_settings.db_path)),
                anilist_ids,
            ),
            episode_number,
            prefix=prefix,
        )
        if not files:
            raise HTTPException(status_code=404, detail="No subtitle files matched this episode request")
        gzip_enabled = wants_gzip(compression=compression, accept_encoding=accept_encoding)
        suffix = ".tar.gz" if gzip_enabled else ".tar"
        prefix_label = f"-{prefix.strip('/').replace('/', '_')}" if prefix else ""
        return archive_response(
            files=files,
            mirror_dir=active_settings.mirror_dir,
            archive_name=f"tvdb-{normalized_kind}-{tvdb_id}-episode-{episode_number}{prefix_label}{suffix}",
            gzip_enabled=gzip_enabled,
        )

    @app.get("/series/tvdb/{media_kind}/{tvdb_id}/content")
    def content_by_tvdb_kind(
        media_kind: str,
        tvdb_id: str,
        prefix: str | None = None,
        compression: str = "auto",
        accept_encoding: str | None = Header(default=None),
    ) -> Response:
        normalized_kind = normalize_media_kind(media_kind)
        anilist_ids = resolve_anilist_ids(
            crosswalk_db_path=active_settings.crosswalk_db_path,
            source="tvdb",
            external_id=tvdb_id,
            media_kind=normalized_kind,
        )
        files = fetch_files_by_anilist_ids_with_prefix(
            get_connection(str(active_settings.db_path)),
            anilist_ids,
            prefix,
        )
        if not files:
            raise HTTPException(status_code=404, detail="No subtitle files matched this series request")
        gzip_enabled = wants_gzip(compression=compression, accept_encoding=accept_encoding)
        suffix = ".tar.gz" if gzip_enabled else ".tar"
        prefix_label = f"-{prefix.strip('/').replace('/', '_')}" if prefix else ""
        return archive_response(
            files=files,
            mirror_dir=active_settings.mirror_dir,
            archive_name=f"tvdb-{normalized_kind}-{tvdb_id}{prefix_label}{suffix}",
            gzip_enabled=gzip_enabled,
        )

    @app.get("/files/content")
    def content_by_refs(
        ref: list[str] = Query(min_length=1),
        compression: str = "auto",
        accept_encoding: str | None = Header(default=None),
    ) -> Response:
        connection = get_connection(str(active_settings.db_path))
        files = [resolve_file_ref(connection, one_ref) for one_ref in ref]
        gzip_enabled = wants_gzip(compression=compression, accept_encoding=accept_encoding)
        suffix = ".tar.gz" if gzip_enabled else ".tar"
        return archive_response(
            files=files,
            mirror_dir=active_settings.mirror_dir,
            archive_name=f"kitsunekko-files{suffix}",
            gzip_enabled=gzip_enabled,
        )

    @app.get("/files/{file_ref}")
    def file_by_ref(file_ref: str) -> dict[str, Any]:
        return resolve_file_ref(get_connection(str(active_settings.db_path)), file_ref)

    @app.get("/files/{file_ref}/content")
    def content_by_ref(
        file_ref: str,
        compression: str = "auto",
        accept_encoding: str | None = Header(default=None),
    ) -> Response:
        file = resolve_file_ref(get_connection(str(active_settings.db_path)), file_ref)
        return single_file_response(
            file=file,
            file_path=mirror_file_path(active_settings.mirror_dir, file),
            gzip_enabled=wants_gzip(compression=compression, accept_encoding=accept_encoding),
        )

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
