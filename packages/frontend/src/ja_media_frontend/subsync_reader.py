from __future__ import annotations

import mimetypes
import socket
import webbrowser
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from ja_media_core.audio import probe_audio_source, resolve_audio_source
from ja_media_core.reader import ReaderSession, reader_session_from_cues
from ja_media_core.transcripts import read_srt


DEFAULT_HOST = "127.0.0.1"
STREAM_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ByteRange:
    """HTTP byte range resolved against a concrete file size."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def run_subsync_reader(
    *,
    media_file: str,
    sub_file: str | None = None,
    host: str = DEFAULT_HOST,
    port: int = 0,
    open_browser: bool = True,
) -> None:
    """Serve the browser subtitle reader for one local media/SRT pair."""

    session = build_reader_session(media_file=media_file, sub_file=sub_file)
    app = create_reader_app(session)
    sock = _bind_socket(host=host, port=port)
    actual_host, actual_port = sock.getsockname()[:2]
    url = f"http://{actual_host}:{actual_port}/"

    print(f"Serving subtitle reader at {url}")
    print("Press Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(url)

    import uvicorn

    config = uvicorn.Config(app, host=host, port=actual_port, log_level="warning")
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


def build_reader_session(*, media_file: str | Path, sub_file: str | Path | None) -> ReaderSession:
    """Resolve local inputs and build the reader session payload."""

    media_path = Path(media_file).expanduser().resolve()
    if not media_path.is_file():
        raise SystemExit(f"reader media file is not a file: {media_path}")

    subtitle_path = discover_subtitle_file(media_path, sub_file=sub_file)
    try:
        cues = read_srt(subtitle_path)
    except ValueError as exc:
        raise SystemExit(f"Could not parse {subtitle_path}: {exc}") from exc

    return reader_session_from_cues(
        media_path=media_path,
        subtitle_path=subtitle_path,
        cues=cues,
        media_duration_s=_probe_duration_s(media_path),
    )


def discover_subtitle_file(media_path: Path, *, sub_file: str | Path | None) -> Path:
    """Resolve the SRT sidecar, preferring an exact stem match.

    The default lookup first checks `episode.srt`, then accepts exactly one
    sibling `episode*.srt` match. Multiple fuzzy matches are rejected so the
    reader does not silently bind to the wrong subtitle file.
    """

    if sub_file is not None:
        subtitle_path = Path(sub_file).expanduser().resolve()
        if not subtitle_path.is_file():
            raise SystemExit(f"reader subtitle file is not a file: {subtitle_path}")
        return subtitle_path

    exact_match = media_path.with_suffix(".srt")
    if exact_match.is_file():
        return exact_match

    matches = sorted(
        path
        for path in media_path.parent.glob(f"{media_path.stem}*.srt")
        if path.is_file()
    )
    if not matches:
        raise SystemExit(
            "Could not autodiscover an SRT sidecar. Expected "
            f"{exact_match} or pass --sub-file."
        )
    if len(matches) > 1:
        match_list = "\n".join(f"  {path}" for path in matches)
        raise SystemExit(
            "Multiple SRT sidecars matched the media stem; pass --sub-file:\n"
            f"{match_list}"
        )
    return matches[0].resolve()


def create_reader_app(session: ReaderSession) -> FastAPI:
    """Create the local-only FastAPI app for a resolved reader session."""

    app = FastAPI(title="ja-media subsync reader")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _static_text("reader.html")

    @app.get("/static/{asset_name}")
    def static_asset(asset_name: str) -> Response:
        if asset_name not in {"reader.css", "reader.js"}:
            raise HTTPException(status_code=404)
        media_type = "text/css" if asset_name.endswith(".css") else "text/javascript"
        return Response(_static_text(asset_name), media_type=media_type)

    @app.get("/session.json")
    def session_json() -> JSONResponse:
        return JSONResponse(session.to_jsonable())

    @app.get("/media")
    @app.head("/media")
    def media(request: Request) -> Response:
        return _media_response(session.media_path, request)

    return app


def _media_response(path: Path, request: Request) -> Response:
    file_size = path.stat().st_size
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    byte_range = parse_range_header(request.headers.get("range"), file_size=file_size)
    if byte_range is None:
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        }
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers, media_type=content_type)
        return StreamingResponse(
            _iter_file_range(path, start=0, end=file_size - 1),
            status_code=200,
            headers=headers,
            media_type=content_type,
        )

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {byte_range.start}-{byte_range.end}/{file_size}",
        "Content-Length": str(byte_range.length),
    }
    if request.method == "HEAD":
        return Response(status_code=206, headers=headers, media_type=content_type)
    return StreamingResponse(
        _iter_file_range(path, start=byte_range.start, end=byte_range.end),
        status_code=206,
        headers=headers,
        media_type=content_type,
    )


def parse_range_header(range_header: str | None, *, file_size: int) -> ByteRange | None:
    """Parse a single HTTP byte range header.

    Invalid or unsatisfiable ranges raise 416. Missing ranges return None so the
    caller can serve the whole file.
    """

    if range_header is None:
        return None
    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416)

    value = range_header.removeprefix("bytes=").split(",", maxsplit=1)[0].strip()
    start_text, separator, end_text = value.partition("-")
    if separator != "-":
        raise HTTPException(status_code=416)

    try:
        if start_text == "":
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError
            start = max(0, file_size - suffix_length)
            end = file_size - 1
        else:
            start = int(start_text)
            end = file_size - 1 if end_text == "" else int(end_text)
    except ValueError as exc:
        raise HTTPException(status_code=416) from exc

    if start < 0 or end < start or start >= file_size:
        raise HTTPException(status_code=416)
    return ByteRange(start=start, end=min(end, file_size - 1))


def _iter_file_range(path: Path, *, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _probe_duration_s(media_path: Path) -> float | None:
    try:
        source = resolve_audio_source(media_path, must_exist=True)
        return probe_audio_source(source).duration_s
    except Exception:
        return None


def _static_text(asset_name: str) -> str:
    return (
        resources.files("ja_media_frontend.static")
        .joinpath(asset_name)
        .read_text(encoding="utf-8")
    )


def _bind_socket(*, host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(2048)
    return sock
