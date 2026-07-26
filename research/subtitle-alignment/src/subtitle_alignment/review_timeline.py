"""Compact two-track timeline adapted from the subsync TimelineWidget."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static


class TimedSpan(Protocol):
    start_s: float
    end_s: float


class ReviewTimeline(Static):
    """Render anchor and selected output with the subsync widget contract."""

    DEFAULT_CSS = "ReviewTimeline { height: auto; padding: 0 1; }"

    def __init__(self) -> None:
        super().__init__()
        self.spans: Sequence[TimedSpan] = ()
        self.reference_spans: Sequence[TimedSpan] = ()
        self.start_s = 0.0
        self.duration_s = 10.0
        self.title = "Timeline"

    def set_timeline(
        self,
        spans: Sequence[TimedSpan],
        *,
        start_s: float,
        duration_s: float,
        title: str,
        reference_spans: Sequence[TimedSpan] = (),
    ) -> None:
        """Match subsync's movable-window presentation seam."""

        self.spans = spans
        self.reference_spans = reference_spans
        self.start_s = max(0.0, start_s)
        self.duration_s = max(0.001, duration_s)
        self.title = title
        self.update(self.render_timeline())

    def render_timeline(self) -> Panel:
        end_s = self.start_s + self.duration_s
        width = max(24, (self.size.width or 96) - 8)
        content = [
            Text(
                f"window {clock(self.start_s)} → {clock(end_s)} "
                f"({self.duration_s:.1f}s)",
                style="bold",
            ),
            Text("embedded anchor", style="dim"),
            self._activity(self.reference_spans, width, self.start_s, end_s, ("grey39", "grey62")),
            Text("selected output", style="dim"),
            self._activity(self.spans, width, self.start_s, end_s, ("green", "cyan", "magenta")),
            self._ticks(width, self.start_s, end_s),
        ]
        return Panel(Group(*content), title=self.title, expand=True)

    @staticmethod
    def _activity(spans, width, start_s, end_s, styles) -> Text:
        text = Text()
        step = (end_s - start_s) / width
        for cell in range(width):
            left, right = start_s + cell * step, start_s + (cell + 1) * step
            best, overlap = None, 0.0
            for index, span in enumerate(spans):
                amount = min(span.end_s, right) - max(span.start_s, left)
                if amount > overlap:
                    best, overlap = index, amount
            text.append("▀" if best is not None else " ", style=styles[best % len(styles)] if best is not None else "dim")
        return text

    @staticmethod
    def _ticks(width: int, start_s: float, end_s: float) -> Text:
        middle = (start_s + end_s) / 2
        labels = (clock(start_s), clock(middle), clock(end_s))
        gap = max(1, width - sum(map(len, labels)))
        return Text(labels[0] + " " * (gap // 2) + labels[1] + " " * (gap - gap // 2) + labels[2], style="dim")


def clock(seconds: float) -> str:
    """Format a subtitle clock without hiding large offsets."""

    milliseconds = max(0, round(seconds * 1000))
    minutes, remainder = divmod(milliseconds, 60_000)
    whole, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{whole:02d}.{millis:03d}"
