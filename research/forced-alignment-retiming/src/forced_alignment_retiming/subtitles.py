"""Parse subtitle files without inventing a new subtitle representation."""

from __future__ import annotations

from pathlib import Path

from ja_media_core.transcripts import SubtitleCue, parse_ass, parse_srt


def read_cues(path: Path, format_name: str) -> tuple[SubtitleCue, ...]:
    """Decode one SRT or ASS file into the repository's existing cue type."""

    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    normalized = format_name.casefold().lstrip(".")
    if normalized in {"srt", "subrip"}:
        return tuple(parse_srt(text, source_path=path))
    if normalized in {"ass", "ssa"}:
        return tuple(parse_ass(text, source_path=path))
    raise ValueError(f"unsupported subtitle format: {format_name}")


def positive_cues(cues: tuple[SubtitleCue, ...]) -> tuple[SubtitleCue, ...]:
    """Keep cues that occupy a positive interval on the source clock."""

    return tuple(cue for cue in cues if cue.end_s > cue.start_s)
