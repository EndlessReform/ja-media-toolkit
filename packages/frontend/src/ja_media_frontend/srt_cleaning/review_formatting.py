from __future__ import annotations

from ja_media_frontend.srt_cleaning.review_models import ReviewCue


def cleaned_text(cue: ReviewCue) -> str:
    """Render the effective cleaned text for the review diff panel."""

    decision = cue.decision
    if decision is None:
        return "<missing decision>"
    if decision.kind == "remove":
        return "<removed>"
    if decision.kind == "edit":
        return decision.text or "<empty edit>"
    if decision.kind in {"as_is", "asis"}:
        return cue.mechanical_text
    if decision.kind == "escalate":
        return cue.original.text + "\n\n<escalated; original preserved>"
    return cue.original.text


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
