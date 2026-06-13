from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


_TIMING_RE = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})(?P<settings>.*)$"
)


@dataclass(frozen=True)
class SubtitleCue:
    """One SRT cue expressed in source-clock seconds.

    The parser keeps cue text and trailing timing settings, but deliberately
    normalizes timestamps to seconds. That gives the rest of the toolkit a
    small, format-agnostic contract for timing work while preserving enough SRT
    detail to write a useful sidecar later.
    """

    source_path: str | None
    index: int
    start_s: float
    end_s: float
    text: str
    timing_settings: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def read_srt(path: str | Path) -> list[SubtitleCue]:
    """Read and parse a UTF-8 SRT file."""

    source_path = Path(path).expanduser().resolve()
    return parse_srt(
        source_path.read_text(encoding="utf-8-sig"),
        source_path=source_path,
    )


def parse_srt(text: str, *, source_path: str | Path | None = None) -> list[SubtitleCue]:
    """Parse SRT text into cue objects.

    SRT in the wild is loose: blank lines split cues, numeric indexes are useful
    but not guaranteed to be trustworthy, and timing arrows may carry optional
    positioning metadata. This parser accepts those common variations while
    rejecting malformed timing blocks clearly enough for CLI/TUI use.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff")
    if not normalized.strip():
        return []

    cues: list[SubtitleCue] = []
    source = None if source_path is None else str(Path(source_path).expanduser().resolve())
    for fallback_index, block in enumerate(_cue_blocks(normalized), start=1):
        lines = block.splitlines()
        if not lines:
            continue

        declared_index: int | None = None
        timing_line_offset = 0
        if lines[0].strip().isdigit() and len(lines) > 1:
            declared_index = int(lines[0].strip())
            timing_line_offset = 1

        timing_line = lines[timing_line_offset] if timing_line_offset < len(lines) else ""
        match = _TIMING_RE.match(timing_line)
        if match is None:
            raise ValueError(
                f"Malformed SRT timing line in block {fallback_index}: {timing_line!r}"
            )

        cue_text = "\n".join(lines[timing_line_offset + 1 :]).strip()
        start_s = parse_srt_timestamp(match.group("start"))
        end_s = parse_srt_timestamp(match.group("end"))
        if end_s < start_s:
            raise ValueError(
                f"SRT cue {declared_index or fallback_index} ends before it starts"
            )

        cues.append(
            SubtitleCue(
                source_path=source,
                index=declared_index or fallback_index,
                start_s=start_s,
                end_s=end_s,
                text=cue_text,
                timing_settings=match.group("settings").rstrip(),
                metadata={"block_index": fallback_index},
            )
        )

    return cues


def format_srt(cues: Iterable[SubtitleCue]) -> str:
    """Format cues as a conventional SRT document with sequential indexes."""

    blocks = []
    for output_index, cue in enumerate(cues, start=1):
        settings = cue.timing_settings
        blocks.append(
            "\n".join(
                [
                    str(output_index),
                    (
                        f"{format_srt_timestamp(cue.start_s)} --> "
                        f"{format_srt_timestamp(cue.end_s)}{settings}"
                    ),
                    cue.text.strip(),
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def shift_srt_cues(
    cues: Iterable[SubtitleCue],
    offset_s: float,
    *,
    negative: str = "reject",
) -> list[SubtitleCue]:
    """Return cues shifted by a constant offset.

    `negative="reject"` protects against silently creating impossible cue
    times. Use `negative="clamp"` for exploratory/manual workflows where cues
    that would cross zero should be pinned to the start of the file.
    """

    if negative not in {"reject", "clamp"}:
        raise ValueError("negative must be 'reject' or 'clamp'")

    shifted = []
    for cue in cues:
        start_s = cue.start_s + offset_s
        end_s = cue.end_s + offset_s
        if start_s < 0 or end_s < 0:
            if negative == "reject":
                raise ValueError(
                    f"Offset {offset_s:+.3f}s makes cue {cue.index} negative"
                )
            start_s = max(0.0, start_s)
            end_s = max(start_s, end_s)
        shifted.append(
            SubtitleCue(
                source_path=cue.source_path,
                index=cue.index,
                start_s=start_s,
                end_s=end_s,
                text=cue.text,
                timing_settings=cue.timing_settings,
                metadata=dict(cue.metadata),
            )
        )
    return shifted


def parse_srt_timestamp(value: str) -> float:
    """Parse an SRT timestamp into seconds."""

    time_part, fraction = value.replace(",", ".").split(".", maxsplit=1)
    hours_text, minutes_text, seconds_text = time_part.split(":")
    fraction_ms = int(fraction.ljust(3, "0")[:3])
    return (
        int(hours_text) * 3600
        + int(minutes_text) * 60
        + int(seconds_text)
        + fraction_ms / 1000
    )


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds as an SRT timestamp."""

    milliseconds_total = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _cue_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip():
            current.append(line)
            continue
        if current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks
