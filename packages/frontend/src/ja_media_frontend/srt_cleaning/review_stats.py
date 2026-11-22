from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ja_media_frontend.srt_cleaning.review_models import ReviewSource


@dataclass(frozen=True)
class ReasonCount:
    """Cue counts for one cleanup reason, split by model decision."""

    reason: str
    edits: int
    removes: int


@dataclass(frozen=True)
class ReasonStats:
    """Small decision/reason summary for one collection of subtitle tracks."""

    cue_count: int
    edits: int
    removes: int
    reasons: tuple[ReasonCount, ...]


def summarize_reasons(sources: Iterable[ReviewSource]) -> ReasonStats:
    """Count edit and remove cues by their saved reasons."""

    cue_count = edits = removes = 0
    counts: Counter[tuple[str, str]] = Counter()
    for source in sources:
        cue_count += len(source.cues)
        for cue in source.cues:
            decision = cue.decision
            if not cue.mechanical_text:
                continue
            if decision is None or decision.kind not in {"edit", "remove"}:
                continue
            if decision.kind == "edit":
                edits += 1
            else:
                removes += 1
            for reason in decision.reasons or ("(no reason)",):
                counts[(reason, decision.kind)] += 1

    reason_names = {reason for reason, _kind in counts}
    reasons = tuple(
        sorted(
            (
                ReasonCount(
                    reason=reason,
                    edits=counts[(reason, "edit")],
                    removes=counts[(reason, "remove")],
                )
                for reason in reason_names
            ),
            key=lambda row: (-(row.edits + row.removes), row.reason),
        )
    )
    return ReasonStats(cue_count, edits, removes, reasons)


def render_reason_stats(title: str, stats: ReasonStats) -> Group:
    """Render counts and within-decision percentages for a stats modal."""

    changed = stats.edits + stats.removes
    summary = Text.assemble(
        (title, "bold cyan"),
        f"  {stats.cue_count:,} cues  ",
        (f"{stats.edits:,} edit", "bold orange3"),
        "  ",
        (f"{stats.removes:,} remove", "bold red"),
    )
    table = Table(expand=True, box=None, pad_edge=False)
    table.add_column("reason", ratio=1)
    table.add_column("edit", justify="right")
    table.add_column("% edits", justify="right")
    table.add_column("remove", justify="right")
    table.add_column("% removes", justify="right")
    table.add_column("changed", justify="right")
    table.add_column("% changed", justify="right")
    for row in stats.reasons:
        row_changed = row.edits + row.removes
        table.add_row(
            row.reason,
            f"{row.edits:,}",
            _percent(row.edits, stats.edits),
            f"{row.removes:,}",
            _percent(row.removes, stats.removes),
            f"{row_changed:,}",
            _percent(row_changed, changed),
        )
    if not stats.reasons:
        table.add_row("No edit or remove reasons", "-", "-", "-", "-", "-", "-")
    return Group(summary, table)


def _percent(value: int, total: int) -> str:
    return f"{value / total:.1%}" if total else "-"
