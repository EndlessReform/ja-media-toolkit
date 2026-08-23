from __future__ import annotations

from difflib import SequenceMatcher

from rich.console import Group
from rich.text import Text

from ja_media_frontend.srt_cleaning.review_models import ReviewCue


TIMELINE_STYLES = {
    "as_is": ("#2f9e44", "#69db7c"),
    "asis": ("#2f9e44", "#69db7c"),
    "edit": ("#f59f00", "#ffd43b"),
    "remove": ("#e03131", "#ff6b6b"),
    "escalate": ("#9c36b5", "#da77f2"),
    "missing": ("#868e96", "#ced4da"),
}


def decision_style(kind: str) -> str:
    if kind == "edit":
        return "bold yellow"
    if kind == "remove":
        return "bold red"
    if kind == "escalate":
        return "bold magenta"
    if kind in {"as_is", "asis"}:
        return "green"
    return "dim"


def timeline_styles(cues: tuple[ReviewCue, ...]) -> tuple[str, ...]:
    """Color each decision consistently while separating adjacent cues."""

    styles = []
    for index, cue in enumerate(cues):
        kind = cue.decision.kind if cue.decision else "missing"
        shades = TIMELINE_STYLES.get(kind, TIMELINE_STYLES["missing"])
        styles.append(shades[index % 2])
    return tuple(styles)


def timeline_legend() -> Text:
    """Explain the decision colors rendered on the cue track."""

    text = Text("cue decisions  ", style="dim")
    for index, (label, kind) in enumerate(
        (("as-is", "as_is"), ("edit", "edit"), ("remove", "remove"),
         ("escalate", "escalate"), ("missing", "missing"))
    ):
        if index:
            text.append("  ")
        text.append(label, style=f"bold {TIMELINE_STYLES[kind][1]}")
    return text


def decision_summary(
    kind: str,
    reasons: tuple[str, ...],
    *,
    compliant: bool,
) -> Text:
    """Render the decision and its reasons as separate visual fields."""

    text = Text.assemble(("decision: ", "bold"), (kind, decision_style(kind)))
    text.append("  reason: ", style="bold")
    if reasons:
        text.append(", ".join(reasons), style="bold cyan")
    else:
        text.append("no reason saved", style="dim")
    if not compliant:
        text.append("  noncompliant row", style="bold red")
    return text


def colored_model_diff(cue: ReviewCue) -> Group:
    """Keep model input literal and show all changes on the cleaned line."""

    if cue.decision is None:
        return Group(
            Text.assemble(("normalized input\n", "bold"), cue.mechanical_text),
            Text.assemble(("cleaned diff\n", "bold"), ("<missing decision>", "dim")),
        )

    before = cue.mechanical_text
    after = cue.display_text
    before_line = Text("normalized input", style="bold")
    if cue.mechanical_rules:
        before_line.append("  ")
        before_line.append(", ".join(cue.mechanical_rules), style="bold cyan")
    before_line.append("\n")
    before_line.append(before)
    after_line = Text("cleaned diff", style="bold")
    if before == after:
        after_line.append("  no model change", style="dim")
    after_line.append("\n")
    for tag, before_start, before_end, after_start, after_end in SequenceMatcher(
        None,
        before,
        after,
        autojunk=False,
    ).get_opcodes():
        before_part = before[before_start:before_end]
        after_part = after[after_start:after_end]
        if tag == "equal":
            after_line.append(after_part)
        else:
            if before_part:
                after_line.append(before_part, style="bold red strike")
            if after_part:
                after_line.append(after_part, style="bold bright_green")
    if not after:
        after_line.append("  <removed>", style="bold red")
    return Group(before_line, after_line)
