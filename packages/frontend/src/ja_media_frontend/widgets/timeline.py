"""A reusable Textual timeline for displaying timed spans."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static


SPAN_STYLES = (
    "green",
    "bright_green",
    "cyan",
    "bright_cyan",
    "magenta",
    "bright_magenta",
    "blue",
    "bright_blue",
)
ACTIVE_SPAN_STYLE = "bold black on yellow"
GAP_STYLE = "dim"
SPAN_BLOCK = "▀"
GAP_BLOCK = " "


class TimedSpan(Protocol):
    """Minimum contract required to render an item on the timeline."""

    start_s: float
    end_s: float


class TimelineWidget(Static):
    """Render timed spans within a movable, zoomable time window.

    The widget owns presentation and overlap calculations only. Applications
    retain navigation state and call :meth:`set_timeline` whenever the selected
    spans or visible window change.
    """

    DEFAULT_CSS = """
    TimelineWidget {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, *, empty_message: str = "No timeline data.", **kwargs) -> None:
        super().__init__(**kwargs)
        self.empty_message = empty_message
        self._spans: Sequence[TimedSpan] = ()
        self._active_span: TimedSpan | None = None
        self._start_s = 0.0
        self._duration_s = 10.0
        self._title = "Timeline"

    def set_timeline(
        self,
        spans: Sequence[TimedSpan],
        *,
        start_s: float,
        duration_s: float,
        title: str = "Timeline",
        active_span: TimedSpan | None = None,
    ) -> None:
        """Replace the rendered timeline state and refresh the widget."""

        self._spans = spans
        self._active_span = active_span
        self._start_s = max(0.0, start_s)
        self._duration_s = max(0.001, duration_s)
        self._title = title
        self.update(self.render_timeline())

    def render_timeline(self) -> Panel:
        """Build the Rich panel representing the current widget state."""

        if not self._spans:
            return Panel(self.empty_message, title=self._title)
        end_s = self._start_s + self._duration_s
        width = self.bar_width()
        lines = [
            Text.assemble(
                ("window: ", "bold"),
                (format_clock(self._start_s), "cyan"),
                " -> ",
                (format_clock(end_s), "cyan"),
                f"  ({self._duration_s:.1f}s shown)",
            ),
            self.activity_bar(
                width=width,
                start_s=self._start_s,
                end_s=end_s,
            ),
            self.tick_bar(width=width, start_s=self._start_s, end_s=end_s),
        ]
        return Panel(Group(*lines), title=self._title, expand=True)

    def bar_width(self) -> int:
        """Return the cells available after widget and panel decoration."""

        available_width = self.size.width
        return max(24, (available_width or 96) - 8)

    def activity_bar(self, *, width: int, start_s: float, end_s: float) -> Text:
        """Render span occupancy, highlighting the active span."""

        text = Text()
        step_s = (end_s - start_s) / width
        for cell in range(width):
            span_index = self._visible_span_index(
                start_s + cell * step_s,
                start_s + (cell + 1) * step_s,
            )
            if span_index is None:
                text.append(GAP_BLOCK, style=GAP_STYLE)
                continue
            span = self._spans[span_index]
            style = (
                ACTIVE_SPAN_STYLE
                if span is self._active_span
                else SPAN_STYLES[span_index % len(SPAN_STYLES)]
            )
            text.append(SPAN_BLOCK, style=style)
        return text

    def tick_bar(self, *, width: int, start_s: float, end_s: float) -> Text:
        """Render start, midpoint, and end labels without overlap clipping."""

        characters = [" " for _ in range(width)]
        labels = (
            format_clock(start_s),
            format_clock((start_s + end_s) / 2),
            format_clock(end_s),
        )
        positions = (
            0,
            max(0, width // 2 - len(labels[1]) // 2),
            max(0, width - len(labels[2])),
        )
        for label, position in zip(labels, positions, strict=True):
            for offset, character in enumerate(label):
                target = position + offset
                if 0 <= target < width:
                    characters[target] = character
        return Text("".join(characters), style="dim")

    def _visible_span_index(
        self,
        cell_start_s: float,
        cell_end_s: float,
    ) -> int | None:
        best_index = None
        best_overlap_s = 0.0
        for index, span in enumerate(self._spans):
            overlap_s = min(span.end_s, cell_end_s) - max(
                span.start_s,
                cell_start_s,
            )
            if overlap_s > best_overlap_s:
                best_index = index
                best_overlap_s = overlap_s
        return best_index


def format_clock(seconds: float) -> str:
    """Format seconds as a compact timeline clock with milliseconds."""

    milliseconds_total = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    if hours:
        return f"{hours:d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"
