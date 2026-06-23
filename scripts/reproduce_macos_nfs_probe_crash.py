#!/usr/bin/env python3
"""Reproduce native metadata-probe crashes against a mounted media file.

This script intentionally imports no ja-media modules. It launches ffprobe and
ExifTool through several subprocess I/O shapes, records negative return codes
as Unix signals, and writes captured output beneath a temporary directory.

Run it through the repository environment:

    uv run scripts/reproduce_macos_nfs_probe_crash.py /path/to/episode.mkv
"""

from __future__ import annotations

import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProbeCase:
    """One executable and argument vector tested through several I/O modes."""

    name: str
    command: tuple[str, ...]


def main() -> int:
    arguments = sys.argv[1:]
    load_project_env = "--load-dotenv" in arguments
    sequence = "--sequence" in arguments
    prompt = "--prompt" in arguments
    import_cli = "--import-cli" in arguments
    fetch_anilist = "--fetch-anilist" in arguments
    resolve_anilist = "--resolve-anilist" in arguments
    connect_anilist = "--connect-anilist" in arguments
    arguments = [item for item in arguments if item != "--load-dotenv"]
    arguments = [item for item in arguments if item != "--sequence"]
    arguments = [item for item in arguments if item != "--prompt"]
    arguments = [item for item in arguments if item != "--import-cli"]
    arguments = [item for item in arguments if item != "--fetch-anilist"]
    arguments = [item for item in arguments if item != "--resolve-anilist"]
    arguments = [item for item in arguments if item != "--connect-anilist"]
    if len(arguments) != 1:
        print(
            f"usage: {Path(sys.argv[0]).name} "
            "[--load-dotenv] [--sequence] [--prompt] [--import-cli] "
            "[--fetch-anilist] [--resolve-anilist] [--connect-anilist] "
            "MEDIA_FILE_OR_DIRECTORY",
            file=sys.stderr,
        )
        return 2
    added_environment_names: list[str] = []
    if load_project_env:
        added_environment_names = _load_project_dotenv()
    if import_cli:
        import ja_media_frontend.cli  # noqa: F401
    if fetch_anilist:
        _fetch_anilist_metadata()
    if resolve_anilist:
        _resolve_anilist_host()
    if connect_anilist:
        _connect_anilist_host()
    source = Path(arguments[0]).expanduser().resolve()
    media_paths = _media_paths(source, sequence)
    if not media_paths:
        print(f"no media files found: {source}", file=sys.stderr)
        return 2
    media = media_paths[0]

    cases = (
        ProbeCase("true-control", ("/usr/bin/true",)),
        ProbeCase(
            "ffprobe",
            (
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                (
                    "format=duration:"
                    "stream=index,codec_type,codec_name,channels,sample_rate:"
                    "stream_tags=language,title:"
                    "stream_disposition=default"
                ),
                "-of",
                "json",
                str(media),
            ),
        ),
        ProbeCase(
            "exiftool",
            ("exiftool", "-j", "-G1", "-a", "-s", str(media)),
        ),
    )

    environment = _environment(media)
    environment["dotenv_loaded"] = load_project_env
    environment["dotenv_added_names"] = added_environment_names
    environment["sequence"] = sequence
    environment["sequence_files"] = len(media_paths)
    environment["prompt"] = prompt
    environment["import_cli"] = import_cli
    environment["fetch_anilist"] = fetch_anilist
    environment["resolve_anilist"] = resolve_anilist
    environment["connect_anilist"] = connect_anilist
    print(json.dumps(environment, indent=2))
    if prompt:
        answer = input("Continue with probes? [y/N] ").strip().casefold()
        if answer not in {"y", "yes"}:
            return 2
    if sequence:
        return _run_sequence(media_paths)
    with tempfile.TemporaryDirectory(prefix="ja-media-probe-repro-") as temp:
        output_dir = Path(temp)
        print(f"output_dir={output_dir}")
        failed = False
        for case in cases:
            for mode in ("inherit", "devnull", "files", "capture"):
                returncode = _run_case(case, mode, output_dir)
                failed = failed or returncode != 0
        print(f"retained_outputs=false temporary_dir={output_dir}")
    return 1 if failed else 0


def _media_paths(source: Path, sequence: bool) -> tuple[Path, ...]:
    if sequence and source.is_dir():
        return tuple(
            sorted(
                (
                    path
                    for path in source.iterdir()
                    if path.is_file() and path.suffix.casefold() == ".mkv"
                ),
                key=lambda path: path.name.casefold(),
            )
        )
    return (source,) if source.is_file() else ()


def _run_sequence(media_paths: tuple[Path, ...]) -> int:
    """Mimic CLI discovery: one captured ExifTool process per sorted MKV."""

    for position, media in enumerate(media_paths, 1):
        print(
            f"\n=== sequence {position}/{len(media_paths)} {media.name} ===",
            flush=True,
        )
        result = subprocess.run(
            ("exiftool", "-j", "-G1", "-a", "-s", str(media)),
            check=False,
            capture_output=True,
        )
        print(f"stdout_bytes={len(result.stdout)} stderr_bytes={len(result.stderr)}")
        print(_returncode_description(result.returncode))
        if result.returncode != 0:
            return 1
    return 0


def _load_project_dotenv() -> list[str]:
    """Load the repository dotenv and return only newly added variable names."""

    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError as error:
        raise SystemExit(
            "--load-dotenv requires python-dotenv; run from packages/frontend"
        ) from error
    before = set(os.environ)
    load_dotenv()
    return sorted(set(os.environ) - before)


def _fetch_anilist_metadata() -> None:
    """Perform the same standard-library HTTP request that precedes CLI probing."""

    from ja_media_core.anilist_search import HttpAniListSearchClient

    metadata = HttpAniListSearchClient().anime(101573)
    print(f"fetched_anilist_id={metadata.anilist_id}")


def _resolve_anilist_host() -> None:
    """Resolve only the configured service hostname, without opening a socket."""

    from ja_media_core.anilist_search import HttpAniListSearchClient

    parsed = urlsplit(HttpAniListSearchClient().base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    print(f"resolved_anilist_addresses={len(addresses)}")


def _connect_anilist_host() -> None:
    """Open and close one TCP connection without sending an HTTP request."""

    from ja_media_core.anilist_search import HttpAniListSearchClient

    parsed = urlsplit(HttpAniListSearchClient().base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    with socket.create_connection((parsed.hostname, port), timeout=5):
        pass
    print("connected_anilist_socket=true")


def _environment(media: Path) -> dict[str, object]:
    stat = media.stat()
    return {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
        "cwd": str(Path.cwd()),
        "media": str(media),
        "media_size": stat.st_size,
        "media_mtime_ns": stat.st_mtime_ns,
        "path": os.environ.get("PATH"),
        "dyld_library_path_set": bool(os.environ.get("DYLD_LIBRARY_PATH")),
        "dyld_insert_libraries_set": bool(os.environ.get("DYLD_INSERT_LIBRARIES")),
    }


def _run_case(case: ProbeCase, mode: str, output_dir: Path) -> int:
    print(f"\n=== {case.name} mode={mode} ===", flush=True)
    if mode == "inherit":
        result = subprocess.run(case.command, check=False)
    elif mode == "devnull":
        result = subprocess.run(
            case.command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    elif mode == "capture":
        result = subprocess.run(
            case.command,
            check=False,
            capture_output=True,
        )
        print(f"captured_stdout_bytes={len(result.stdout)}")
        print(f"captured_stderr_bytes={len(result.stderr)}")
    elif mode == "files":
        stdout_path = output_dir / f"{case.name}.stdout"
        stderr_path = output_dir / f"{case.name}.stderr"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            result = subprocess.run(
                case.command,
                check=False,
                stdout=stdout,
                stderr=stderr,
            )
        print(f"stdout_file_bytes={stdout_path.stat().st_size}")
        print(f"stderr_file_bytes={stderr_path.stat().st_size}")
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(_returncode_description(result.returncode))
    return result.returncode


def _returncode_description(returncode: int) -> str:
    if returncode >= 0:
        return f"returncode={returncode}"
    number = -returncode
    try:
        name = signal.Signals(number).name
    except ValueError:
        name = "UNKNOWN"
    return f"returncode={returncode} signal={number} signal_name={name}"


if __name__ == "__main__":
    raise SystemExit(main())
