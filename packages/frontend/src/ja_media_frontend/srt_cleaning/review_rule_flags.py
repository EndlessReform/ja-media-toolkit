from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table

from ja_media_frontend.srt_cleaning.candidate_rules import apply_candidate_rules
from ja_media_frontend.srt_cleaning.review_models import ReviewCue, ReviewWorkspace


@dataclass(frozen=True)
class FlagSummaryRow:
    """Whole-run counts for one suspicious-cue screening flag."""

    name: str
    cues: int
    sources: int
    windows: int


@dataclass(frozen=True)
class FlagSummary:
    """Preview of the cues and saved model windows a filtered run would touch."""

    total_cues: int
    total_windows: int
    flagged_cues: int
    flagged_windows: int
    rows: tuple[FlagSummaryRow, ...]


def candidate_flags(cue: ReviewCue) -> tuple[str, ...]:
    """Screen the same normalized cue text shown in the rule overlay."""

    return apply_candidate_rules(cue.mechanical_text).flags


def cue_has_candidate_flags(cue: ReviewCue) -> bool:
    return bool(candidate_flags(cue))


def summarize_candidate_flags(workspace: ReviewWorkspace) -> FlagSummary:
    """Count suspicious cues and the saved windows containing them."""

    total_cues = 0
    all_windows: set[tuple[str, int]] = set()
    flagged_cue_keys: set[tuple[str, int]] = set()
    flagged_windows: set[tuple[str, int]] = set()
    matches: dict[str, list[tuple[str, int, int | None]]] = {}
    for source in workspace.sources:
        source_key = f"{source.subtitle_id}:{source.source_sha256}"
        for cue in source.cues:
            total_cues += 1
            decision = cue.decision
            window = decision.window_number if decision else None
            if window is not None:
                all_windows.add((source_key, window))
            flags = candidate_flags(cue)
            if not flags:
                continue
            flagged_cue_keys.add((source_key, cue.original.index))
            if window is not None:
                flagged_windows.add((source_key, window))
            for flag in flags:
                matches.setdefault(flag, []).append(
                    (source_key, cue.original.index, window)
                )

    rows = tuple(
        FlagSummaryRow(
            name=name,
            cues=len(values),
            sources=len({value[0] for value in values}),
            windows=len({(value[0], value[2]) for value in values if value[2] is not None}),
        )
        for name, values in sorted(matches.items(), key=lambda item: (-len(item[1]), item[0]))
    )
    return FlagSummary(
        total_cues=total_cues,
        total_windows=len(all_windows),
        flagged_cues=len(flagged_cue_keys),
        flagged_windows=len(flagged_windows),
        rows=rows,
    )


def render_candidate_flag_summary(summary: FlagSummary) -> Table:
    """Render the filtered-rollout preview in the existing rule modal."""

    table = Table(
        title="Suspicious-cue flags — filtered-run preview",
        expand=True,
        box=None,
        pad_edge=False,
    )
    table.add_column("flag")
    table.add_column("cues", justify="right")
    table.add_column("share of all cues", justify="right")
    table.add_column("sources", justify="right")
    table.add_column("saved windows touched", justify="right")
    table.add_row(
        "any flag",
        f"{summary.flagged_cues:,}",
        _count_percent(summary.flagged_cues, summary.total_cues),
        "—",
        _count_percent(summary.flagged_windows, summary.total_windows),
        style="bold",
    )
    for row in summary.rows:
        table.add_row(
            row.name,
            f"{row.cues:,}",
            _count_percent(row.cues, summary.total_cues),
            f"{row.sources:,}",
            _count_percent(row.windows, summary.total_windows),
        )
    return table


def _count_percent(count: int, total: int) -> str:
    percent = 100 * count / total if total else 0.0
    return f"{count:,} ({percent:.1f}%)"
