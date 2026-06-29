from __future__ import annotations

from typing import Iterable
from xml.sax.saxutils import escape

from ja_media_core.transcripts import SubtitleCue

from ja_media_frontend.srt_cleaning.contracts import CueWindow


def render_window_prompt(window: CueWindow, *, series_context: str) -> str:
    """Render cue context in a stable XML-ish format with local active IDs."""

    sections = [
        series_context.strip(),
        (
            "Return exactly one decision for each cue in <active>. "
            "Use only the cue's local id attribute. Do not split one cue into "
            "multiple decisions, merge cues, renumber cues, or create decisions "
            "for <context_before> or <context_after>. Use decision as_is, not "
            "edit, when the cue text should stay exactly the same."
        ),
    ]
    if window.before:
        sections.append(render_context_cues("context_before", window.before))
    sections.append(render_active_cues(window.active))
    if window.after:
        sections.append(render_context_cues("context_after", window.after))
    return "\n\n".join(section for section in sections if section)


def render_active_cues(cues: Iterable[SubtitleCue]) -> str:
    lines = [
        render_cue_tag(cue, cue_id=cue_id)
        for cue_id, cue in enumerate(cues, start=1)
    ]
    return "<active>\n" + "\n".join(lines) + "\n</active>"


def render_context_cues(tag: str, cues: Iterable[SubtitleCue]) -> str:
    cue_list = list(cues)
    lines = [f'<{tag} count="{len(cue_list)}">']
    lines.extend(render_cue_tag(cue) for cue in cue_list)
    lines.append(f"</{tag}>")
    return "\n".join(lines)


def render_cue_tag(cue: SubtitleCue, *, cue_id: int | None = None) -> str:
    id_attribute = f'id="{cue_id}" ' if cue_id is not None else ""
    return (
        f'<cue {id_attribute}start="{cue.start_s:.3f}" end="{cue.end_s:.3f}">'
        f"{escape(cue.text)}</cue>"
    )
