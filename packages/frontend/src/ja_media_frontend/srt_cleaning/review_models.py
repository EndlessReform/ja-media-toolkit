from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ja_media_core.transcripts import SubtitleCue


@dataclass(frozen=True)
class ReviewDecision:
    """One cleaning decision joined to a source-clock subtitle cue."""

    kind: str
    text: str | None
    reasons: tuple[str, ...] = ()
    custom_id: str | None = None
    local_id: int | None = None
    window_number: int | None = None
    compliant: bool = True
    mechanical_text: str | None = None
    mechanically_changed: bool = False
    mechanical_rules: tuple[str, ...] = ()
    model_text_matches_mechanical: bool | None = None
    served_model: str | None = None


@dataclass(frozen=True)
class ReviewAlignment:
    """Forced-aligner candidate timing for one stable source cue."""

    start_s: float
    end_s: float
    status: str
    token_count: int = 0
    score_signals: dict[str, Any] | None = None
    window_index: int | None = None
    window_kind: str | None = None
    candidate_count: int = 1


@dataclass(frozen=True)
class ReviewCue:
    """Original cue plus the model's cleaned-text decision, if any."""

    original: SubtitleCue
    decision: ReviewDecision | None
    mechanical_text: str
    mechanically_changed: bool
    mechanical_rules: tuple[str, ...] = ()
    alignment: ReviewAlignment | None = None

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

    @property
    def playback_cue(self) -> SubtitleCue:
        """Play candidate timing when loaded, otherwise the source cue timing."""

        return self.cue_with_timing(use_alignment=True)

    def cue_with_timing(self, *, use_alignment: bool) -> SubtitleCue:
        """Project this cue onto original or forced-aligned borders."""

        if not use_alignment or self.alignment is None:
            return self.original
        return SubtitleCue(
            source_path=self.original.source_path,
            index=self.original.index,
            start_s=self.alignment.start_s,
            end_s=self.alignment.end_s,
            text=self.display_text,
            metadata=dict(self.original.metadata),
        )


@dataclass(frozen=True)
class ReviewSource:
    """One original SRT candidate and its reconstructed review artifacts."""

    anilist_id: int
    subtitle_id: str
    repo_path: str
    filename: str
    source_path: Path
    cleaned_path: Path | None
    episode_number: int | None
    source_sha256: str
    cues: tuple[ReviewCue, ...]
    alignment_path: Path | None = None
    alignment_audio_path: Path | None = None

    @property
    def label(self) -> str:
        return self.repo_path or self.filename or self.subtitle_id

    @property
    def active_s(self) -> float:
        return sum(cue.end_s - cue.start_s for cue in self.cues)

    @property
    def end_s(self) -> float:
        return max((cue.end_s for cue in self.cues), default=0.0)

    def end_s_for_timing(self, *, use_alignment: bool) -> float:
        """Return the end of the original or aligned cue track."""

        return max(
            (
                cue.cue_with_timing(use_alignment=use_alignment).end_s
                for cue in self.cues
            ),
            default=0.0,
        )

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

    def sources_for_episode(
        self, anilist_id: int, episode_number: int
    ) -> tuple[ReviewSource, ...]:
        """Return candidate subtitles for one series/episode pair."""

        return tuple(
            source
            for source in self.sources
            if source.anilist_id == anilist_id
            and source.episode_number == episode_number
        )

    def preferred_source_index(self, anilist_id: int, episode_number: int) -> int:
        """Open the aligned candidate when an episode has several subtitles."""

        sources = self.sources_for_episode(anilist_id, episode_number)
        return next(
            (index for index, source in enumerate(sources) if source.alignment_path),
            0,
        )

    def preferred_cue_indices(self) -> dict[str, int]:
        """Start aligned sources on their first candidate rather than an exclusion."""

        return {
            source.subtitle_id: next(
                (index for index, cue in enumerate(source.cues) if cue.alignment),
                0,
            )
            for source in self.sources
            if source.alignment_path
        }

    @property
    def episodes(self) -> tuple[int, ...]:
        values = sorted(
            {source.episode_number for source in self.sources if source.episode_number}
        )
        return tuple(values)

    @property
    def episode_keys(self) -> tuple[tuple[int, int], ...]:
        """Return every series/episode pair represented by the run."""

        return tuple(
            sorted(
                {
                    (source.anilist_id, source.episode_number)
                    for source in self.sources
                    if source.episode_number is not None
                }
            )
        )
