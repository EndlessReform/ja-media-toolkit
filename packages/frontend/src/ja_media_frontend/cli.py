from __future__ import annotations

import argparse
import logging
from typing import Callable, NoReturn


DEFAULT_APPLE_VAD_MODEL = "mlx-community/silero-vad"
_LOG = logging.getLogger("ja_media_frontend")


def main() -> None:
    """Run the shared `ja-media` command-line frontend.

    This package owns the user-facing parser. Runtime-specific packages such as
    `ja-media-apple` own the concrete work and are imported only after the user
    selects a command that needs them.
    """

    _configure_logging()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "vad-local":
        _load_apple_command("run_vad_local")(args)
        return
    if args.command == "transcribe":
        _load_apple_command("run_transcribe")(args)
        return
    if args.command == "get-id":
        from ja_media_frontend.anilist_search_cli import run_search

        query = args.query
        if args.file:
            if query:
                from rich.console import Console
                Console(stderr=True).print("[bold red]Error:[/bold red] Provide either a search query OR a file path (-f), not both.")
                return
            import PTN
            from pathlib import Path
            from rich.console import Console
            console = Console(stderr=True)
            stem = Path(args.file).stem
            parsed = PTN.parse(stem)
            query = parsed.get("title")
            if not query:
                console.print(f"[bold red]Error:[/bold red] Could not parse a title from filename [yellow]{stem}[/yellow]")
                return

        if not query:
            from rich.console import Console
            Console(stderr=True).print("[bold red]Error:[/bold red] No search query provided. Use a positional argument or -f.")
            return

        run_search(
            query=query,
            top_k=args.top_k,
            include_movies=args.include_movies,
            include_ova=args.include_ova,
            all_formats=args.all_formats,
            force_anilist=args.force_anilist,
            output_format=args.format,
        )
        return
    if args.command == "subsync":
        if args.subsync_command == "reader":
            from ja_media_frontend.subsync.reader import run_subsync_reader

            run_subsync_reader(
                media_file=args.media,
                sub_file=args.sub_file,
                host=args.host,
                port=args.port,
                open_browser=not args.no_open,
            )
            return
        if args.subsync_command == "tui":
            from ja_media_frontend.subsync.tui import run_subsync_tui

            run_subsync_tui(
                source_path=args.source,
                srt_inputs=args.srt,
                window_s=args.window_s,
                anilist_id=args.anilist,
                tvdb_id=args.tvdb,
                episode_number=args.episode,
                audio_profile=args.audio_profile,
                fetch_subs=args.fetch_subs,
                tvdb_media_kind=args.tvdb_kind,
                sort_by_language=args.sort_by_language,
                vocal_separation=args.vocal_separation,
            )
            return
        args.subsync_parser.print_help()
        return
    if args.command == "audio-library":
        if args.audio_library_command == "ingest":
            from ja_media_frontend.audio_library.cli import run_audio_library_ingest

            run_audio_library_ingest(args)
            return
        args.audio_library_parser.print_help()
        return

    parser.print_help()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ja-media",
        description="Japanese media management and transcription utilities",
    )
    subparsers = parser.add_subparsers(dest="command")

    from ja_media_frontend.audio_library.cli import register_audio_library_parser
    from ja_media_frontend.anilist_search_cli import register_get_id_parser
    from ja_media_frontend.cli_parsers import register_subsync_parser

    register_subsync_parser(subparsers)
    register_audio_library_parser(subparsers)
    register_get_id_parser(subparsers)

    vad_parser = subparsers.add_parser(
        "vad-local",
        help="Run local MLX VAD on a client-local audio file",
    )
    vad_parser.add_argument("input", help="Local audio file path")
    vad_parser.add_argument(
        "-c",
        "--config",
        help="Path to ja-media-toolkit TOML config. Defaults to JA_MEDIA_CONFIG or XDG config.",
    )
    vad_parser.add_argument("--start-s", type=float, default=0.0)
    vad_parser.add_argument("--end-s", type=float)
    vad_parser.add_argument("--threshold", type=float)
    vad_parser.add_argument("--min-speech-s", type=float)
    vad_parser.add_argument("--min-silence-s", type=float)
    vad_parser.add_argument("--speech-pad-s", type=float)
    vad_parser.add_argument("--merge-gap-s", type=float)
    vad_parser.add_argument("--channel", type=int)
    vad_parser.add_argument("--model-id", default=DEFAULT_APPLE_VAD_MODEL)
    vad_parser.add_argument(
        "--dump-speech-dir",
        help=(
            "Write output chunks as audio files: detected speech spans in plain "
            "VAD mode, planned split chunks with --split-every-minutes"
        ),
    )
    vad_parser.add_argument(
        "--dump-audio-format",
        choices=("wav", "flac"),
        default="wav",
        help="Audio format for dumped chunks. WAV is the default for macOS playback.",
    )
    vad_parser.add_argument(
        "--split-every-minutes",
        type=float,
        help="Plan cuts near every N minutes using bounded VAD search windows",
    )
    vad_parser.add_argument(
        "--split-radius-s",
        type=float,
        default=60.0,
        help="Seconds to inspect on each side of each split target",
    )
    vad_parser.add_argument(
        "--prefer-before-target",
        action="store_true",
        help="Prefer silence before the target when cut candidates tie",
    )
    vad_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
    )

    transcribe_parser = subparsers.add_parser(
        "transcribe",
        help="Run the configured Apple ASR backend on a client-local audio file",
    )
    transcribe_parser.add_argument(
        "input",
        nargs="+",
        help="Local audio file path or glob pattern. Quote globs to let ja-media expand them.",
    )
    transcribe_parser.add_argument(
        "-c",
        "--config",
        help="Path to ja-media-toolkit TOML config. Defaults to JA_MEDIA_CONFIG or XDG config.",
    )
    transcribe_parser.add_argument(
        "--backend",
        help="Configured ASR backend name. Defaults to [asr].default_backend.",
    )
    transcribe_parser.add_argument("--start-s", type=float, default=0.0)
    transcribe_parser.add_argument("--end-s", type=float)
    transcribe_parser.add_argument("--language", default="ja")
    transcribe_parser.add_argument("--context-info")
    transcribe_parser.add_argument("--hotword", action="append", default=[])
    transcribe_parser.add_argument(
        "--max-concurrent-requests",
        type=int,
        help="Override the selected ASR backend's concurrent vLLM request limit.",
    )
    transcribe_parser.add_argument(
        "--startup-only",
        action="store_true",
        help="Load config/model and print startup metadata without calling vLLM.",
    )
    transcribe_parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
    )
    transcribe_parser.add_argument(
        "--srt-dir",
        help="Write one .srt file per transcribed input into this directory.",
    )

    return parser


def _load_apple_command(name: str) -> Callable[[argparse.Namespace], None]:
    try:
        from ja_media_apple import cli as apple_cli
    except ModuleNotFoundError as exc:
        if exc.name == "ja_media_apple":
            _missing_apple_backend()
        raise
    return getattr(apple_cli, name)


def _missing_apple_backend() -> NoReturn:
    raise SystemExit(
        "This command currently needs the Apple backend. Run it from the "
        "ja-media-apple runtime or install ja-media-apple alongside the frontend."
    )


def _configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    for logger_name in ("httpx", "httpcore", "huggingface_hub", "urllib3"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    try:
        from rich.console import Console
        from rich.logging import RichHandler
    except ModuleNotFoundError:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        _LOG.debug("Rich logging is not installed; using plain logging.")
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            RichHandler(
                console=Console(stderr=True),
                markup=True,
                show_path=False,
                show_time=True,
            )
        ],
    )


if __name__ == "__main__":
    main()
