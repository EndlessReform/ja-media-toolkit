from __future__ import annotations

from difflib import SequenceMatcher

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from ja_media_frontend.srt_cleaning.review_dialogs import RuleStatsModal
from ja_media_frontend.srt_cleaning.review_formatting import (
    colored_model_diff,
    decision_summary,
)
from ja_media_frontend.srt_cleaning.review_models import ReviewCue
from ja_media_frontend.srt_cleaning.review_rule_comparison import (
    compare_candidate_rules,
    render_rule_summary,
    summarize_candidate_rules,
)
from ja_media_frontend.srt_cleaning.review_rule_flags import (
    candidate_flags,
    cue_has_candidate_flags,
    render_candidate_flag_summary,
    summarize_candidate_flags,
)
from ja_media_frontend.widgets.timeline import format_clock


class RuleOverlayMixin:
    """Toggle and summarize candidate rules inside the existing cue review."""

    def action_toggle_rule_overlay(self) -> None:
        self.rule_overlay = not self.rule_overlay
        self.action_show_cue_review()
        self.refresh_view()

    def action_show_rule_stats(self) -> None:
        summary = summarize_candidate_rules(self.workspace)
        flags = summarize_candidate_flags(self.workspace)
        self.push_screen(
            RuleStatsModal(
                Group(
                    render_rule_summary(
                        f"Whole run: {self.workspace.run_id}",
                        summary,
                    ),
                    Text(""),
                    render_candidate_flag_summary(flags),
                )
            )
        )

    def action_next_flagged(self) -> None:
        self.move_flagged(1)

    def action_previous_flagged(self) -> None:
        self.move_flagged(-1)

    def move_flagged(self, delta: int) -> None:
        source = self.source
        if source is None or not source.cues:
            return
        current = self.cue_index(source)
        for distance in range(1, len(source.cues) + 1):
            index = (current + delta * distance) % len(source.cues)
            if cue_needs_review(source.cues[index]):
                self.stop_playback()
                self.cue_indices[source.subtitle_id] = index
                self.ensure_cue_visible()
                self.refresh_view()
                return
        self.notify("No cleaning or alignment flags in this source")


def cue_needs_review(cue: ReviewCue) -> bool:
    """Include failed alignment geometry in the existing flagged-cue walk."""

    return (
        cue.alignment is not None and cue.alignment.status != "aligned"
    ) or cue_has_candidate_flags(cue)


def render_cue_panel(
    cue: ReviewCue | None,
    *,
    playing: bool,
    rule_overlay: bool,
    timing_mode: str = "aligned",
) -> Panel:
    """Render the standard cue comparison with an optional rule projection."""

    if cue is None:
        return Panel("No cue selected.", title="Original vs cleaned")
    original = cue.original
    decision = cue.decision
    uses_alignment = timing_mode == "aligned" and cue.alignment is not None
    header = Text(f"{original.index}\n", style="bold cyan")
    header.append(
        f"Original subtitle borders: {format_clock(original.start_s)} -> "
        f"{format_clock(original.end_s)}",
        style="bold yellow" if timing_mode == "original" else "cyan",
    )
    if timing_mode == "original":
        header.append("  ACTIVE", style="bold yellow")
        if playing:
            header.append(" / PLAYING", style="bold orange3")
    if cue.alignment is not None:
        delta_start = cue.alignment.start_s - original.start_s
        delta_end = cue.alignment.end_s - original.end_s
        header.append(
            f"\nForced-aligned borders: "
            f"{format_clock(cue.alignment.start_s)} -> "
            f"{format_clock(cue.alignment.end_s)}",
            style="bold yellow" if uses_alignment else "yellow",
        )
        if uses_alignment:
            header.append("  ACTIVE", style="bold yellow")
            if playing:
                header.append(" / PLAYING", style="bold orange3")
        header.append(
            f"\nMoved from original: start {_movement_phrase(delta_start)}; "
            f"end {_movement_phrase(delta_end)}",
            style="white",
        )
        scores = cue.alignment.score_signals or {}
        reversed_count = int(scores.get("reversed_token_count", 0))
        backward_count = int(scores.get("backward_token_count", 0))
        if cue.alignment.status != "aligned":
            warning = "The aligner returned text timestamps in a broken order."
            if reversed_count or backward_count:
                warning = (
                    f"{_count_phrase(reversed_count, 'text piece')} "
                    f"{_verb(reversed_count, 'ends', 'end')} before it starts; "
                    f"{_count_phrase(backward_count, 'text piece')} "
                    f"{_verb(backward_count, 'starts', 'start')} before the "
                    "previous one."
                )
            header.append(
                f"\nNEEDS TIMING REVIEW — {warning} "
                "This is a timestamp-order warning, not a confidence result.",
                style="bold red",
            )
        else:
            header.append(
                "\nTimestamp order looks consistent. This does not confirm that "
                "the subtitle text is present in the audio.",
                style="green",
            )
        if cue.alignment.window_index is not None:
            window_description = {
                "core": "main audio window",
                "boundary": "second pass around a speech break",
            }.get(cue.alignment.window_kind or "", "audio window")
            header.append(
                f"\nTiming source: {window_description} "
                f"#{cue.alignment.window_index}; selected from "
                f"{cue.alignment.candidate_count} candidate timings.",
                style="magenta",
            )
        if scores:
            header.append(
                "\nExperimental model scores (not pass/fail): "
                f"weakest chosen-time probability "
                f"{scores['min_endpoint_max_probability']:.3f}; "
                f"smallest lead over the next time "
                f"{scores['min_top_two_probability_margin']:.3f}; "
                f"widest uncertainty {scores['max_normalized_entropy']:.3f}; "
                f"closest chosen time to an audio edge "
                f"{scores['min_edge_distance_s']:.2f}s.",
                style="dim cyan",
            )
            header.append(
                f"\nAligner split: {cue.alignment.token_count} text pieces. "
                f"Timestamp shape: {scores['zero_duration_token_count']} zero-length; "
                f"{reversed_count} end-before-start; "
                f"{scores['repeated_timestamp_count']} reused times; "
                f"{backward_count} out-of-order starts.",
                style="dim",
            )
    elif timing_mode == "aligned":
        header.append(
            "\nNot present in the materialized aligned subtitle.",
            style="bold red",
        )
    kind = decision.kind if decision else "missing"
    comparison = rule_comparison_diff(cue) if rule_overlay else colored_model_diff(cue)
    body = Group(
        header,
        decision_summary(
            kind,
            decision.reasons if decision else (),
            compliant=decision.compliant if decision else True,
        ),
        Text.assemble(("original\n", "bold"), original.text or "<empty cue>"),
        comparison,
    )
    title = "Original vs cleaned — rule overlay" if rule_overlay else "Original vs cleaned"
    return Panel(body, title=title, expand=True)


def _movement_phrase(delta_s: float) -> str:
    if abs(delta_s) < 0.005:
        return "unchanged"
    direction = "later" if delta_s > 0 else "earlier"
    return f"{abs(delta_s):.2f}s {direction}"


def _count_phrase(count: int, singular: str) -> str:
    suffix = "" if count == 1 else "s"
    return f"{count} {singular}{suffix}"


def _verb(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def rule_comparison_diff(cue: ReviewCue) -> Group:
    """Compare saved and proposed outputs against the same current input."""

    comparison = compare_candidate_rules(cue)
    model = cue.decision.served_model if cue.decision else None
    model_label = model or "saved model"
    baseline_line = Text(f"current model input — sent to {model_label}", style="bold")
    if cue.mechanical_rules:
        baseline_line.append("  ")
        baseline_line.append(", ".join(cue.mechanical_rules), style="bold cyan")
    baseline_line.append("\n")
    baseline_line.append(comparison.baseline)

    saved_line = Text(f"current model output — returned by {model_label}", style="bold")
    saved_line.append("\n")
    if comparison.target is None:
        saved_line.append("<cannot compare this decision>", style="dim")
    else:
        _append_diff(saved_line, comparison.baseline, comparison.target)
        if not comparison.target:
            saved_line.append("  <removed>", style="bold red")

    proposed_line = Text("proposed deterministic output — simulation only", style="bold")
    proposed_line.append(f"  {_status_label(comparison.status)}", style="bold cyan")
    if comparison.candidate.rules:
        proposed_line.append("  ")
        proposed_line.append(", ".join(comparison.candidate.rules), style="dim cyan")
    flags = candidate_flags(cue)
    if flags:
        proposed_line.append("\nflagged for filtered review  ", style="bold cyan")
        proposed_line.append(", ".join(flags), style="bold yellow")
    proposed_line.append("\n")
    _append_diff(proposed_line, comparison.baseline, comparison.candidate.text)
    if not comparison.candidate.text:
        proposed_line.append("  <removed>", style="bold red")
    return Group(baseline_line, saved_line, proposed_line)


def _append_diff(line: Text, before: str, after: str) -> None:
    for tag, before_start, before_end, after_start, after_end in SequenceMatcher(
        None,
        before,
        after,
        autojunk=False,
    ).get_opcodes():
        before_part = before[before_start:before_end]
        after_part = after[after_start:after_end]
        if tag == "equal":
            line.append(after_part)
        else:
            if before_part:
                line.append(before_part, style="bold red strike")
            if after_part:
                line.append(after_part, style="bold bright_green")


def _status_label(status: str) -> str:
    return {
        "exact": "same as saved model",
        "partial": "partly matches saved model",
        "no_rule": "no proposed rule fired",
        "accepted_changed": "rule changed model as-is",
        "wrong": "disagrees with saved model",
        "unscored": "cannot compare",
    }[status]
