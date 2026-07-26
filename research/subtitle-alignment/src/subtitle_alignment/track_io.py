"""Small subtitle-file boundary shared by survey and executable adapters."""

from __future__ import annotations

from pathlib import Path

from ja_media_core.transcripts import SubtitleCue, parse_ass, parse_srt


def read_subtitle_cues(path: Path, format_name: str) -> tuple[SubtitleCue, ...]:
    """Decode cached subtitle bytes and parse the declared serialization."""

    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    normalized = format_name.casefold().lstrip(".")
    cues = (
        parse_srt(text)
        if normalized in {"srt", "subrip"}
        else parse_ass(text)
    )
    return tuple(cues)
