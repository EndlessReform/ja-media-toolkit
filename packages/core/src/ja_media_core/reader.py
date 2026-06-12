from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from ja_media_core.srt import SubtitleCue


TimelineTrackKind = Literal["media", "subtitle"]


@dataclass(frozen=True)
class TimelineSpan:
    """A visible span on a reader timeline track.

    Timeline spans are deliberately small: source-clock bounds plus enough
    metadata for a frontend to link the span back to a cue or source track.
    The shape is suitable for browser rendering, TUI rendering, or later saved
    review manifests without committing to any one UI implementation.
    """

    start_s: float
    end_s: float
    label: str
    cue_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class TimelineTrack:
    """One horizontal lane in the reader timeline."""

    id: str
    label: str
    kind: TimelineTrackKind
    spans: list[TimelineSpan]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def active_s(self) -> float:
        return sum(max(0.0, span.duration_s) for span in self.spans)

    @property
    def end_s(self) -> float:
        return max((span.end_s for span in self.spans), default=0.0)


@dataclass(frozen=True)
class ReaderSession:
    """Platform-agnostic episode reader state served to a browser UI."""

    media_path: Path
    subtitle_path: Path
    cues: list[SubtitleCue]
    timeline_tracks: list[TimelineTrack]
    media_duration_s: float | None = None
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def timeline_end_s(self) -> float:
        ends = [track.end_s for track in self.timeline_tracks]
        if self.media_duration_s is not None:
            ends.append(self.media_duration_s)
        return max(ends, default=0.0)

    def to_jsonable(self) -> dict[str, Any]:
        """Return a JSON-friendly payload with paths rendered as strings."""

        payload = asdict(self)
        payload["media_path"] = str(self.media_path)
        payload["subtitle_path"] = str(self.subtitle_path)
        payload["timeline_end_s"] = self.timeline_end_s
        payload["title"] = self.title or self.media_path.stem
        return payload


def reader_session_from_cues(
    *,
    media_path: str | Path,
    subtitle_path: str | Path,
    cues: list[SubtitleCue],
    media_duration_s: float | None = None,
    title: str | None = None,
) -> ReaderSession:
    """Build the default two-lane reader timeline for one media/SRT pair."""

    media = Path(media_path).expanduser().resolve()
    subtitle = Path(subtitle_path).expanduser().resolve()
    cue_end_s = max((cue.end_s for cue in cues), default=0.0)
    timeline_end_s = max(media_duration_s or 0.0, cue_end_s)
    media_spans = []
    if timeline_end_s > 0:
        media_spans.append(TimelineSpan(start_s=0.0, end_s=timeline_end_s, label="media"))

    subtitle_spans = [
        TimelineSpan(
            start_s=cue.start_s,
            end_s=cue.end_s,
            label=str(cue.index),
            cue_index=index,
            metadata={"srt_index": cue.index},
        )
        for index, cue in enumerate(cues)
    ]

    return ReaderSession(
        media_path=media,
        subtitle_path=subtitle,
        cues=cues,
        timeline_tracks=[
            TimelineTrack(id="media", label=media.name, kind="media", spans=media_spans),
            TimelineTrack(
                id="subtitle",
                label=subtitle.name,
                kind="subtitle",
                spans=subtitle_spans,
            ),
        ],
        media_duration_s=media_duration_s,
        title=title or media.stem,
    )
