from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ja_media_core.transcripts import SubtitleCue


@dataclass(frozen=True)
class ReviewDecision:
    """One cleaning decision joined to a source-clock subtitle cue."""

    kind: str
    text: str | None
    category: str | None
    custom_id: str | None = None
    local_id: int | None = None
    window_number: int | None = None
    compliant: bool = True
    mechanical_text: str | None = None
    mechanically_changed: bool = False
    mechanical_rules: tuple[str, ...] = ()
    model_text_matches_mechanical: bool | None = None


@dataclass(frozen=True)
class ReviewCue:
    """Original cue plus the model's cleaned-text decision, if any."""

    original: SubtitleCue
    decision: ReviewDecision | None
    mechanical_text: str
    mechanically_changed: bool
    mechanical_rules: tuple[str, ...] = ()

    @property
    def start_s(self) -> float:
        return self.original.start_s

    @property
    def end_s(self) -> float:
        return self.original.end_s

    @property
    def display_text(self) -> str:
        decision = self.decision
        if decision is None:
            return self.original.text
        if decision.kind in {"as_is", "asis"}:
            return self.mechanical_text
        if decision.kind == "escalate":
            return self.original.text
        if decision.kind == "edit":
            return decision.text or ""
        if decision.kind == "remove":
            return ""
        return self.original.text

    @property
    def changed(self) -> bool:
        decision = self.decision
        return self.mechanically_changed or (
            decision is not None and decision.kind in {"edit", "remove"}
        )


@dataclass(frozen=True)
class ReviewSource:
    """One original SRT candidate and its reconstructed review artifacts."""

    subtitle_id: str
    repo_path: str
    filename: str
    source_path: Path
    cleaned_path: Path | None
    episode_number: int | None
    source_sha256: str
    cues: tuple[ReviewCue, ...]

    @property
    def label(self) -> str:
        return self.repo_path or self.filename or self.subtitle_id

    @property
    def active_s(self) -> float:
        return sum(cue.end_s - cue.start_s for cue in self.cues)

    @property
    def end_s(self) -> float:
        return max((cue.end_s for cue in self.cues), default=0.0)

    @property
    def changed_count(self) -> int:
        return sum(cue.changed for cue in self.cues)


@dataclass(frozen=True)
class ReviewWorkspace:
    """All reviewable sources discovered for one SRT cleaning run."""

    anilist_id: int
    run_id: str
    run_dir: Path
    sources: tuple[ReviewSource, ...]

    @property
    def episodes(self) -> tuple[int, ...]:
        values = sorted(
            {source.episode_number for source in self.sources if source.episode_number}
        )
        return tuple(values)
