from __future__ import annotations

from dataclasses import dataclass
import re

from ja_media_core.transcripts import SubtitleCue


TRAILING_ARROW_RE = re.compile(r"[ \t]*[➡➜➝➞➟➠➢➣➤➥➦➧➨➩➪➫➬➭➮➯➱➲➳➵➸➺➻➼➽➾→]+[ \t]*$")


@dataclass(frozen=True)
class MechanicalNormalization:
    """Deterministic subtitle typography cleanup applied before model review."""

    text: str
    changed: bool
    rules: tuple[str, ...]


def mechanically_normalize_text(text: str) -> MechanicalNormalization:
    """Return the model-visible baseline for a raw SRT cue."""

    rules: list[str] = []
    normalized = text
    if "\n" in normalized or "\r" in normalized:
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        normalized = normalized.replace("\n", "")
        rules.append("join_physical_lines")
    stripped = TRAILING_ARROW_RE.sub("", normalized)
    if stripped != normalized:
        normalized = stripped
        rules.append("strip_trailing_arrow")
    return MechanicalNormalization(
        text=normalized,
        changed=normalized != text,
        rules=tuple(rules),
    )


def mechanically_normalize_cue(cue: SubtitleCue) -> SubtitleCue:
    """Return a cue with deterministic text cleanup and unchanged timing."""

    normalized = mechanically_normalize_text(cue.text)
    if not normalized.changed:
        return cue
    return SubtitleCue(
        source_path=cue.source_path,
        index=cue.index,
        start_s=cue.start_s,
        end_s=cue.end_s,
        text=normalized.text,
        timing_settings=cue.timing_settings,
        metadata=dict(cue.metadata),
    )
