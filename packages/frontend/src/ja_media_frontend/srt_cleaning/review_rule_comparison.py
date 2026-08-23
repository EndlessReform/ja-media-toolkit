from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ja_media_frontend.srt_cleaning.candidate_rules import (
    CANDIDATE_RULES,
    CandidateRuleResult,
    apply_candidate_rules,
)
from ja_media_frontend.srt_cleaning.review_models import ReviewCue, ReviewWorkspace


COMPARISON_STYLES = {
    "flagged": ("#0b7285", "#3bc9db"),
    "exact": ("#2f9e44", "#69db7c"),
    "partial": ("#e67700", "#ffd43b"),
    "no_rule": ("#495057", "#868e96"),
    "accepted_changed": ("#c92a2a", "#ff6b6b"),
    "wrong": ("#862e9c", "#da77f2"),
    "unscored": ("#1864ab", "#74c0fc"),
}


@dataclass(frozen=True)
class RuleComparison:
    """One candidate-rule output compared with the saved model target."""

    baseline: str
    candidate: CandidateRuleResult
    target: str | None
    status: str


@dataclass(frozen=True)
class RuleSummaryRow:
    """Aggregate comparison counts for one isolated rule or the full profile."""

    name: str
    fired: int
    exact: int
    partial: int
    wrong: int
    accepted_changed: int
    unscored: int
    changed_exact: int
    changed_total: int


@dataclass(frozen=True)
class ReasonRuleSummary:
    """Combined-profile results for cues carrying one saved model reason."""

    reason: str
    cues: int
    exact: int
    partial: int
    wrong: int
    no_rule: int


@dataclass(frozen=True)
class RuleSummary:
    """Whole-run candidate-rule comparison used by the review modal."""

    rows: tuple[RuleSummaryRow, ...]
    reasons: tuple[ReasonRuleSummary, ...]


def compare_candidate_rules(
    cue: ReviewCue,
    *,
    enabled_rules: tuple[str, ...] = CANDIDATE_RULES,
) -> RuleComparison:
    """Compare candidate cleanup with the saved model output for one cue."""

    baseline = cue.mechanical_text
    candidate = apply_candidate_rules(baseline, enabled_rules=enabled_rules)
    target = _model_target(cue)
    if not candidate.changed:
        status = "no_rule"
    elif target is None:
        status = "unscored"
    elif candidate.text == target:
        status = "exact"
    elif cue.decision and cue.decision.kind in {"as_is", "asis"}:
        status = "accepted_changed"
    elif _similarity(candidate.text, target) > _similarity(baseline, target):
        status = "partial"
    else:
        status = "wrong"
    return RuleComparison(baseline, candidate, target, status)


def rule_comparison_needs_review(cue: ReviewCue) -> bool:
    """Return whether overlay navigation should stop on this cue."""

    comparison = compare_candidate_rules(cue)
    return comparison.candidate.flagged or comparison.status not in {"exact", "no_rule"}


def summarize_candidate_rules(workspace: ReviewWorkspace) -> RuleSummary:
    """Score isolated and combined candidate rules over a saved review run."""

    cues = tuple(cue for source in workspace.sources for cue in source.cues)
    profiles = (("combined", CANDIDATE_RULES),) + tuple(
        (rule, (rule,)) for rule in CANDIDATE_RULES
    )
    rows = tuple(_summarize_profile(name, rules, cues) for name, rules in profiles)

    reason_matches: dict[str, list[RuleComparison]] = {}
    for cue in cues:
        decision = cue.decision
        if decision is None or decision.kind not in {"edit", "remove"}:
            continue
        comparison = compare_candidate_rules(cue)
        for reason in decision.reasons or ("(no reason)",):
            reason_matches.setdefault(reason, []).append(comparison)
    reasons = tuple(
        sorted(
            (
                ReasonRuleSummary(
                    reason=reason,
                    cues=len(matches),
                    exact=sum(match.status == "exact" for match in matches),
                    partial=sum(match.status == "partial" for match in matches),
                    wrong=sum(match.status == "wrong" for match in matches),
                    no_rule=sum(match.status == "no_rule" for match in matches),
                )
                for reason, matches in reason_matches.items()
            ),
            key=lambda row: (-row.cues, row.reason),
        )
    )
    return RuleSummary(rows, reasons)


def render_rule_summary(title: str, summary: RuleSummary) -> Group:
    """Render whole-run rule scores and their saved-reason breakdown."""

    profiles = Table(title=title, expand=True, box=None, pad_edge=False)
    profiles.add_column("rule")
    profiles.add_column("fired", justify="right")
    profiles.add_column("same as model", justify="right")
    profiles.add_column("partial match", justify="right")
    profiles.add_column("disagrees", justify="right")
    profiles.add_column("changed as-is", justify="right")
    profiles.add_column("cannot compare", justify="right")
    profiles.add_column("model changes matched", justify="right")
    for row in summary.rows:
        profiles.add_row(
            row.name,
            f"{row.fired:,}",
            _count_percent(row.exact, row.fired),
            _count_percent(row.partial, row.fired),
            _count_percent(row.wrong, row.fired),
            f"{row.accepted_changed:,}",
            f"{row.unscored:,}",
            _count_percent(row.changed_exact, row.changed_total),
        )

    reasons = Table(
        title="Combined profile by saved reason",
        expand=True,
        box=None,
        pad_edge=False,
    )
    reasons.add_column("reason")
    reasons.add_column("changed cues", justify="right")
    reasons.add_column("same as model", justify="right")
    reasons.add_column("partial match", justify="right")
    reasons.add_column("disagrees", justify="right")
    reasons.add_column("no rule", justify="right")
    for row in summary.reasons:
        reasons.add_row(
            row.reason,
            f"{row.cues:,}",
            _count_percent(row.exact, row.cues),
            _count_percent(row.partial, row.cues),
            _count_percent(row.wrong, row.cues),
            _count_percent(row.no_rule, row.cues),
        )
    return Group(profiles, Text(""), reasons)


def rule_timeline_styles(cues: tuple[ReviewCue, ...]) -> tuple[str, ...]:
    """Color the existing cue rail by candidate-rule comparison status."""

    styles = []
    for index, cue in enumerate(cues):
        comparison = compare_candidate_rules(cue)
        status = "flagged" if comparison.candidate.flagged else comparison.status
        shades = COMPARISON_STYLES[status]
        styles.append(shades[index % 2])
    return tuple(styles)


def rule_timeline_legend() -> Text:
    """Explain comparison colors on the existing cue rail."""

    text = Text("rule overlay  ", style="bold cyan")
    for index, (label, status) in enumerate(
        (
            ("flagged for review", "flagged"),
            ("same as model", "exact"),
            ("partial match", "partial"),
            ("no rule", "no_rule"),
            ("changed model as-is", "accepted_changed"),
            ("disagrees", "wrong"),
            ("cannot compare", "unscored"),
        )
    ):
        if index:
            text.append("  ")
        text.append(label, style=f"bold {COMPARISON_STYLES[status][1]}")
    return text


def _summarize_profile(
    name: str,
    rules: tuple[str, ...],
    cues: tuple[ReviewCue, ...],
) -> RuleSummaryRow:
    comparisons = tuple(
        compare_candidate_rules(cue, enabled_rules=rules) for cue in cues
    )
    fired = tuple(match for match in comparisons if match.candidate.changed)
    changed = tuple(
        match
        for match in comparisons
        if match.target is not None and match.baseline != match.target
    )
    return RuleSummaryRow(
        name=name,
        fired=len(fired),
        exact=sum(match.status == "exact" for match in fired),
        partial=sum(match.status == "partial" for match in fired),
        wrong=sum(match.status == "wrong" for match in fired),
        accepted_changed=sum(match.status == "accepted_changed" for match in fired),
        unscored=sum(match.status == "unscored" for match in fired),
        changed_exact=sum(match.status == "exact" for match in changed),
        changed_total=len(changed),
    )


def _model_target(cue: ReviewCue) -> str | None:
    decision = cue.decision
    if decision is None or decision.kind == "escalate":
        return None
    if decision.kind in {"as_is", "asis"}:
        return cue.mechanical_text
    if decision.kind == "edit":
        return decision.text or ""
    if decision.kind == "remove":
        return ""
    return None


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _count_percent(count: int, total: int) -> str:
    percent = 100 * count / total if total else 0.0
    return f"{count:,} ({percent:.1f}%)"
