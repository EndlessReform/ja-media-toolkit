from __future__ import annotations

from rich.text import Text
from textual.widgets import ContentSwitcher, DataTable, Static


class ReviewTabMixin:
    """Switch between cue review and corpus-level exploration views."""

    @staticmethod
    def render_tab_bar(active: str) -> Text:
        text = Text()
        text.append(
            " F5 Cue Review ",
            style="bold reverse" if active == "cue-review" else "bold",
        )
        text.append("  ")
        text.append(
            " F6 Reason Pivot ",
            style="bold reverse" if active == "reason-pivot" else "bold",
        )
        return text

    def action_show_cue_review(self) -> None:
        self.query_one("#review-views", ContentSwitcher).current = "cue-review"
        self.query_one("#review-tabs", Static).update(self.render_tab_bar("cue-review"))

    def action_show_reason_pivot(self) -> None:
        self.stop_playback()
        self.query_one("#review-views", ContentSwitcher).current = "reason-pivot"
        self.query_one("#review-tabs", Static).update(self.render_tab_bar("reason-pivot"))
        self.query_one("#reason-pivot-table", DataTable).focus()
