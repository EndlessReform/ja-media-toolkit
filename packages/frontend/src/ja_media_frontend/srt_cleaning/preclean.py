from __future__ import annotations

from dataclasses import dataclass

from ja_media_core.transcripts import SubtitleCue

from ja_media_frontend.srt_cleaning.candidate_rules import apply_candidate_rules
from ja_media_frontend.srt_cleaning.normalization import mechanically_normalize_text


@dataclass(frozen=True)
class PrecleanedText:
    """Model-visible cue text plus the deterministic work that produced it."""

    text: str
    rules: tuple[str, ...]
    flags: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.rules)


def preclean_text(text: str) -> PrecleanedText:
    """Apply the approved deterministic cleanup before model review."""

    mechanical = mechanically_normalize_text(text)
    candidate = apply_candidate_rules(mechanical.text)
    return PrecleanedText(
        text=candidate.text,
        rules=(*mechanical.rules, *candidate.rules),
        flags=candidate.flags,
    )


def preclean_cue(cue: SubtitleCue) -> tuple[SubtitleCue, PrecleanedText]:
    """Return a model-visible cue without changing its timing or source index."""

    prepared = preclean_text(cue.text)
    if prepared.text == cue.text:
        return cue, prepared
    return (
        SubtitleCue(
            source_path=cue.source_path,
            index=cue.index,
            start_s=cue.start_s,
            end_s=cue.end_s,
            text=prepared.text,
            timing_settings=cue.timing_settings,
            metadata=dict(cue.metadata),
        ),
        prepared,
    )
