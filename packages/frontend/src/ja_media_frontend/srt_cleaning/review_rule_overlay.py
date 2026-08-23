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
) -> Panel:
    """Render the standard cue comparison with an optional rule projection."""

    if cue is None:
        return Panel("No cue selected.", title="Original vs cleaned")
    original = cue.original
    decision = cue.decision
    header = Text(
        f"{original.index}  {format_clock(original.start_s)} -> "
        f"{format_clock(original.end_s)}",
        style="bold cyan",
    )
    if playing:
        header.append(" PLAY", style="bold orange3")
    if cue.alignment is not None:
        delta_start = cue.alignment.start_s - original.start_s
        delta_end = cue.alignment.end_s - original.end_s
        header.append(
            f"\nretimed {format_clock(cue.alignment.start_s)} -> "
            f"{format_clock(cue.alignment.end_s)}  "
            f"Δ {delta_start:+.2f}s/{delta_end:+.2f}s  "
            f"{cue.alignment.token_count} tokens  {cue.alignment.status}",
            style=(
                "bold red"
                if cue.alignment.status != "aligned"
                else "bold yellow"
            ),
        )
        if cue.alignment.window_index is not None:
            header.append(
                f"  {cue.alignment.window_kind or 'window'} "
                f"{cue.alignment.window_index}  "
                f"chosen from {cue.alignment.candidate_count}",
                style="cyan",
            )
        scores = cue.alignment.score_signals or {}
        if scores:
            header.append(
                "\nscore signals  "
                f"p(min) {scores['min_endpoint_max_probability']:.3f}  "
                f"margin(min) {scores['min_top_two_probability_margin']:.3f}  "
                f"entropy(max) {scores['max_normalized_entropy']:.3f}  "
                f"edge(min) {scores['min_edge_distance_s']:.2f}s  "
                f"zero/reverse/repeat/back {scores['zero_duration_token_count']}/"
                f"{scores.get('reversed_token_count', 0)}/"
                f"{scores['repeated_timestamp_count']}/"
                f"{scores['backward_token_count']}",
                style="cyan",
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
