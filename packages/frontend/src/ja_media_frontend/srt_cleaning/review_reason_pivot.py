from __future__ import annotations

from dataclasses import dataclass

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Static

from ja_media_frontend.srt_cleaning.review_formatting import colored_model_diff
from ja_media_frontend.srt_cleaning.review_models import (
    ReviewCue,
    ReviewSource,
    ReviewWorkspace,
)


@dataclass(frozen=True)
class ReasonMatch:
    """One changed cue carrying a saved cleanup reason."""

    source: ReviewSource
    cue: ReviewCue


@dataclass(frozen=True)
class ReasonPivotRow:
    """Counts, corpus coverage, and matching cues for one reason."""

    reason: str
    matches: tuple[ReasonMatch, ...]
    edits: int
    removes: int
    series: int
    episodes: int
    sources: int


@dataclass(frozen=True)
class ReasonPivot:
    """Reason rows and the changed-cue denominator used for percentages."""

    changed_cues: int
    rows: tuple[ReasonPivotRow, ...]


def build_reason_pivot(workspace: ReviewWorkspace) -> ReasonPivot:
    """Group edit and removal cues by saved reason across the whole run."""

    changed_cues = 0
    grouped: dict[str, list[ReasonMatch]] = {}
    for source in workspace.sources:
        for cue in source.cues:
            decision = cue.decision
            if not cue.mechanical_text:
                continue
            if decision is None or decision.kind not in {"edit", "remove"}:
                continue
            changed_cues += 1
            for reason in decision.reasons or ("(no reason)",):
                grouped.setdefault(reason, []).append(ReasonMatch(source, cue))

    rows = []
    for reason, matches in grouped.items():
        rows.append(
            ReasonPivotRow(
                reason=reason,
                matches=tuple(matches),
                edits=sum(match.cue.decision.kind == "edit" for match in matches),
                removes=sum(match.cue.decision.kind == "remove" for match in matches),
                series=len({match.source.anilist_id for match in matches}),
                episodes=len(
                    {
                        (match.source.anilist_id, match.source.episode_number)
                        for match in matches
                    }
                ),
                sources=len({match.source.subtitle_id for match in matches}),
            )
        )
    rows.sort(key=lambda row: (-len(row.matches), row.reason))
    return ReasonPivot(changed_cues, tuple(rows))


class ReasonPivotExplorer(Widget):
    """Corpus reason table with cue-by-cue examples for the selected label."""

    BINDINGS = [
        Binding("n,pagedown", "next_match", "Next matching cue", show=False),
        Binding("N,pageup", "previous_match", "Previous matching cue", show=False),
        Binding("j", "next_reason", "Next reason", show=False),
        Binding("k", "previous_reason", "Previous reason", show=False),
    ]

    DEFAULT_CSS = """
    ReasonPivotExplorer { height: 1fr; }
    ReasonPivotExplorer > Vertical { height: 1fr; }
    #reason-pivot-summary { height: auto; padding: 0 1; background: $surface; }
    #reason-pivot-table { height: 3fr; }
    #reason-pivot-detail { height: 2fr; min-height: 10; padding: 0 1; }
    #reason-pivot-help {
        height: auto; padding: 0 1; background: $surface; color: $text-muted;
    }
    """

    def __init__(self, workspace: ReviewWorkspace, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self.pivot = build_reason_pivot(workspace)
        self.row_index = 0
        self.match_indexes: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="reason-pivot-summary")
            yield DataTable(
                cursor_type="row",
                zebra_stripes=True,
                id="reason-pivot-table",
            )
            yield Static(id="reason-pivot-detail")
            yield Static(
                "arrows or j/k select reason  n/N or PgDn/PgUp browse matching cues  "
                "F5 cue review  q quit",
                id="reason-pivot-help",
            )

    def on_mount(self) -> None:
        table = self.query_one("#reason-pivot-table", DataTable)
        table.add_columns(
            "reason",
            "cues",
            "% changed",
            "edit",
            "remove",
            "series",
            "episodes",
            "sources",
        )
        for index, row in enumerate(self.pivot.rows):
            table.add_row(
                row.reason,
                f"{len(row.matches):,}",
                _percent(len(row.matches), self.pivot.changed_cues),
                f"{row.edits:,}",
                f"{row.removes:,}",
                str(row.series),
                str(row.episodes),
                str(row.sources),
                key=str(index),
            )
        self.query_one("#reason-pivot-summary", Static).update(
            Text.assemble(
                ("Cleanup reasons", "bold cyan"),
                f"  {self.pivot.changed_cues:,} changed cues  ",
                f"{len(self.pivot.rows):,} reasons  ",
                "multi-reason cues appear in each matching row",
            )
        )
        if self.pivot.rows:
            table.move_cursor(row=0)
            self._render_detail()

    def on_data_table_row_highlighted(
        self,
        event: DataTable.RowHighlighted,
    ) -> None:
        self.row_index = int(str(event.row_key.value))
        self._render_detail()

    def action_next_match(self) -> None:
        self._move_match(1)

    def action_previous_match(self) -> None:
        self._move_match(-1)

    def action_next_reason(self) -> None:
        self._move_reason(1)

    def action_previous_reason(self) -> None:
        self._move_reason(-1)

    def _move_reason(self, delta: int) -> None:
        if not self.pivot.rows:
            return
        table = self.query_one("#reason-pivot-table", DataTable)
        table.move_cursor(row=max(0, min(self.row_index + delta, len(self.pivot.rows) - 1)))

    def _move_match(self, delta: int) -> None:
        if not self.pivot.rows:
            return
        row = self.pivot.rows[self.row_index]
        current = self.match_indexes.get(row.reason, 0)
        self.match_indexes[row.reason] = (current + delta) % len(row.matches)
        self._render_detail()

    def _render_detail(self) -> None:
        if not self.is_mounted or not self.pivot.rows:
            return
        row = self.pivot.rows[self.row_index]
        match_index = self.match_indexes.get(row.reason, 0)
        match = row.matches[match_index]
        decision = match.cue.decision
        header = Text.assemble(
            (f"AniList {match.source.anilist_id}", "bold cyan"),
            f"  episode {match.source.episode_number}  cue {match.cue.original.index}",
            f"  {decision.kind if decision else 'missing'}\n",
            (match.source.filename, "dim"),
        )
        content = Group(
            header,
            Text.assemble(("original\n", "bold"), match.cue.original.text),
            colored_model_diff(match.cue),
        )
        self.query_one("#reason-pivot-detail", Static).update(
            Panel(
                content,
                title=(
                    f"{row.reason}  cue {match_index + 1:,}/{len(row.matches):,}  "
                    f"{_percent(len(row.matches), self.pivot.changed_cues)} of changed"
                ),
                expand=True,
            )
        )


def _percent(value: int, total: int) -> str:
    return f"{value / total:.1%}" if total else "-"
