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
    if args.command == "subsync":
        if args.subsync_command == "tui":
            _load_apple_subsync_tui()(
                source_path=args.source,
                srt_inputs=args.srt,
                window_s=args.window_s,
            )
            return
        args.subsync_parser.print_help()
        return

    parser.print_help()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ja-media",
        description="Japanese media management and transcription utilities",
    )
    subparsers = parser.add_subparsers(dest="command")

    subsync_parser = subparsers.add_parser(
        "subsync",
        help="Subtitle synchronization review and repair tools",
    )
    subsync_subparsers = subsync_parser.add_subparsers(dest="subsync_command")
    subsync_tui_parser = subsync_subparsers.add_parser(
        "tui",
        help="Open the first-pass subtitle timing review TUI",
    )
    subsync_tui_parser.add_argument("source", help="Source media file path")
    subsync_tui_parser.add_argument(
        "srt",
        nargs="+",
        help=(
            "SRT file path(s) or quoted glob pattern(s), for example "
            "'../../subs/*.srt'"
        ),
    )
    subsync_tui_parser.add_argument(
        "--window-s",
        type=float,
        default=120.0,
        help="Initial number of subtitle timeline seconds shown on screen.",
    )
    subsync_tui_parser.set_defaults(subsync_parser=subsync_parser)

    vad_parser = subparsers.add_parser(
        "vad-local",
        help="Run local MLX VAD on a client-local audio file",
    )
    vad_parser.add_argument("input", help="Local audio file path")
    vad_parser.add_argument("--start-s", type=float, default=0.0)
    vad_parser.add_argument("--end-s", type=float)
    vad_parser.add_argument("--threshold", type=float)
    vad_parser.add_argument("--min-speech-s", type=float, default=0.25)
    vad_parser.add_argument("--min-silence-s", type=float, default=0.20)
    vad_parser.add_argument("--speech-pad-s", type=float, default=0.05)
    vad_parser.add_argument("--merge-gap-s", type=float, default=0.10)
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


def _load_apple_subsync_tui() -> Callable[..., None]:
    try:
        from ja_media_apple.subsync_tui import run_subsync_tui
    except ModuleNotFoundError as exc:
        if exc.name == "ja_media_apple":
            _missing_apple_backend()
        raise
    return run_subsync_tui


def _missing_apple_backend() -> NoReturn:
    raise SystemExit(
        "This command currently needs the Apple backend. Install the frontend "
        "with the Apple extra, for example: uv tool install 'ja-media-frontend[apple]'."
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
