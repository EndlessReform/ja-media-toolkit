from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pysrt


@dataclass(frozen=True)
class SubtitleCue:
    """One subtitle cue expressed in source-clock seconds.

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

    Parsing is delegated to :mod:`pysrt`; this function is the adapter between
    that format-specific model and the toolkit's small, stable cue contract.
    Missing indexes receive their document-order index, and timing-line
    positioning settings are retained for later serialization.
    """

    normalized = text.lstrip("\ufeff")
    if not normalized.strip():
        return []

    cues: list[SubtitleCue] = []
    source = None if source_path is None else str(Path(source_path).expanduser().resolve())
    try:
        parsed = pysrt.from_string(normalized, error_handling=pysrt.ERROR_RAISE)
    except pysrt.Error as exc:
        raise ValueError(f"Malformed SRT: {exc}") from exc

    for fallback_index, item in enumerate(parsed, start=1):
        index = item.index if isinstance(item.index, int) else fallback_index
        start_s = item.start.ordinal / 1000
        end_s = item.end.ordinal / 1000
        if end_s < start_s:
            raise ValueError(
                f"SRT cue {index} ends before it starts"
            )

        cues.append(
            SubtitleCue(
                source_path=source,
                index=index,
                start_s=start_s,
                end_s=end_s,
                text=item.text.strip(),
                timing_settings=f" {item.position}" if item.position else "",
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

    return pysrt.SubRipTime.from_string(value).ordinal / 1000


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds as an SRT timestamp."""

    milliseconds_total = max(0, round(seconds * 1000))
    return str(pysrt.SubRipTime.from_ordinal(milliseconds_total))
