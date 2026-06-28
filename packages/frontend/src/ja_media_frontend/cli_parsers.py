"""Focused argparse registration helpers for the shared frontend."""

from __future__ import annotations

import argparse


def register_subsync_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register subtitle synchronization commands without bloating ``cli.py``."""

    parser = subparsers.add_parser(
        "subsync",
        help="Subtitle synchronization review and repair tools",
    )
    commands = parser.add_subparsers(dest="subsync_command")
    reader = commands.add_parser(
        "reader",
        help="Open a browser reader for one media file and subtitle sidecar",
    )
    reader.add_argument("media", help="Source media file path")
    reader.add_argument(
        "--sub-file",
        help="Subtitle file path. Defaults to media stem autodiscovery.",
    )
    reader.add_argument(
        "--host",
        default="127.0.0.1",
        help="Local interface to bind. Defaults to 127.0.0.1.",
    )
    reader.add_argument(
        "--port",
        type=int,
        default=0,
        help="Local port to bind. Defaults to a random free port.",
    )
    reader.add_argument(
        "--no-open",
        action="store_true",
        help="Serve without opening a browser.",
    )
    reader.set_defaults(subsync_parser=parser)

    tui = commands.add_parser(
        "tui",
        help="Open the first-pass subtitle timing review TUI",
    )
    tui.add_argument(
        "source",
        nargs="?",
        help="Source media file path. Optional with --anilist and --episode.",
    )
    tui.add_argument(
        "srt",
        nargs="*",
        help=(
            "SRT/ASS path(s) or quoted glob pattern(s), for example "
            "'../../subs/*.srt'. Optional when using --fetch-subs or F6 lookup."
        ),
    )
    tui.add_argument(
        "--anilist",
        type=int,
        help=(
            "AniList series ID for Kitsunekko lookup. Defaults to the nearest "
            "anilist-<id> parent directory when present."
        ),
    )
    tui.add_argument("--tvdb", type=int, help="TVDB series ID for subtitle lookup.")
    tui.add_argument(
        "--tvdb-kind",
        default="tv",
        help="TVDB media kind passed to the subtitle service. Defaults to tv.",
    )
    tui.add_argument(
        "--episode",
        type=int,
        help="Episode override. Defaults to parsing the media filename stem.",
    )
    tui.add_argument(
        "--audio-profile",
        default="portable-aac-v1",
        help="Derived audio profile to prefer. Defaults to portable-aac-v1.",
    )
    tui.add_argument(
        "--fetch-subs",
        action="store_true",
        help="Fetch matching Kitsunekko candidates before opening the TUI.",
    )
    tui.add_argument(
        "--sort-by-language",
        action="store_true",
        help=(
            "Sort candidates by subtitle language: Japanese, unknown, "
            "bilingual, non-Japanese, then insufficient text."
        ),
    )
    tui.add_argument(
        "--window-s",
        type=float,
        default=120.0,
        help="Initial subtitle timeline seconds shown on screen.",
    )
    tui.add_argument(
        "--vocal-separation",
        action="store_true",
        help=(
            "Run Demucs vocal separation before opening the TUI to produce a "
            "cleaner VAD/playback source. Off by default; adds seconds-to-minutes "
            "of startup time depending on episode length and accelerator."
        ),
    )
    tui.set_defaults(subsync_parser=parser)
