from __future__ import annotations

import unittest
from dataclasses import dataclass

from ja_media_frontend.widgets.timeline import (
    ACTIVE_SPAN_STYLE,
    GAP_BLOCK,
    SPAN_BLOCK,
    TimelineWidget,
    format_clock,
)


@dataclass(frozen=True)
class Span:
    start_s: float
    end_s: float


class TimelineWidgetTest(unittest.TestCase):
    def test_accepts_arbitrary_timed_spans(self) -> None:
        first = Span(0.0, 1.0)
        second = Span(2.0, 3.0)
        widget = TimelineWidget()
        widget.set_timeline(
            [first, second],
            start_s=0.0,
            duration_s=4.0,
            active_span=second,
        )

        bar = widget.activity_bar(width=8, start_s=0.0, end_s=4.0)

        self.assertEqual(
            bar.plain,
            f"{SPAN_BLOCK}{SPAN_BLOCK}{GAP_BLOCK}{GAP_BLOCK}"
            f"{SPAN_BLOCK}{SPAN_BLOCK}{GAP_BLOCK}{GAP_BLOCK}",
        )
        self.assertIn(ACTIVE_SPAN_STYLE, [span.style for span in bar.spans])

    def test_clock_formats_sub_hour_and_hour_values(self) -> None:
        self.assertEqual(format_clock(65.25), "01:05.250")
        self.assertEqual(format_clock(3_665.25), "1:01:05.250")
