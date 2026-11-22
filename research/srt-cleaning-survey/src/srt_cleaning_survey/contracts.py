from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ja_media_core.transcripts import SubtitleCue


PIPELINE_VERSION = "clean:v2"
OPENAI_CHAT_COMPLETIONS_URL = "/v1/chat/completions"
DEFAULT_MAX_REQUESTS_PER_SHARD = 50_000
DEFAULT_MAX_BYTES_PER_SHARD = 200 * 1000 * 1000

DecisionKind = Literal["as_is", "edit", "remove", "escalate"]
CleanupReason = Literal[
    "speaker_label",
    "sign_caption",
    "lyrics",
    "sfx",
    "paratext",
    "bilingual",
    "censored",
    "multiple_speakers",
    "punctuation",
    "quotation_style",
    "reading_gloss",
    "formatting",
    "typography",
    "other",
]


class CleanDecision(BaseModel):
    """One model decision for one active cue's local prompt ID."""

    model_config = ConfigDict(extra="forbid")

    cue_id: int = Field(alias="id")
    decision: DecisionKind
    text: str | None
    reasons: list[CleanupReason]

    @model_validator(mode="after")
    def validate_decision_shape(self) -> CleanDecision:
        """Reject combinations that cannot produce a useful cleanup record."""

        if self.decision == "as_is":
            if self.text is not None or self.reasons:
                raise ValueError("as_is requires null text and no reasons")
        elif self.decision == "edit":
            if not self.text:
                raise ValueError("edit requires nonempty text")
            if not self.reasons:
                raise ValueError("edit requires at least one reason")
        elif self.decision == "remove":
            if not self.reasons:
                raise ValueError("remove requires at least one reason")
            if "other" in self.reasons and not self.text:
                raise ValueError(
                    "remove with reason other requires an explanation in text"
                )
            if "other" not in self.reasons and self.text is not None:
                raise ValueError("remove requires null text unless its reason is other")
        elif self.decision == "escalate":
            if not self.text:
                raise ValueError("escalate requires an explanation in text")
        return self


class CleanWindowResult(BaseModel):
    """Structured result expected from one subtitle cleaning batch row."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[CleanDecision]


@dataclass(frozen=True)
class SourceDocument:
    """A cached source SRT plus the metadata needed for stable manifests."""

    anilist_id: int
    subtitle_id: str
    repo_path: str
    filename: str
    source_path: Path
    metadata_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CueWindow:
    """One non-overlapping active cue span with optional prompt context."""

    source: SourceDocument
    window_number: int
    active: tuple[SubtitleCue, ...]
    before: tuple[SubtitleCue, ...]
    after: tuple[SubtitleCue, ...]
    source_sha256: str
    prompt_policy_sha256: str
    original_active_texts: tuple[str, ...] = ()
    active_rules: tuple[tuple[str, ...], ...] = ()
    active_flags: tuple[tuple[str, ...], ...] = ()

    @property
    def cue_start_index(self) -> int:
        return self.active[0].index

    @property
    def cue_end_index(self) -> int:
        return self.active[-1].index

    @property
    def active_indexes(self) -> tuple[int, ...]:
        return tuple(cue.index for cue in self.active)

    @property
    def custom_id(self) -> str:
        active_hash = sha256_text(
            "\n".join(f"{cue.index}\n{cue.text}" for cue in self.active)
        )[:16]
        return (
            f"{PIPELINE_VERSION}:anilist-{self.source.anilist_id}:"
            f"srt-{safe_id(self.source.subtitle_id)}:"
            f"w{self.window_number:05d}:"
            f"{self.cue_start_index}-{self.cue_end_index}:"
            f"policy-{self.prompt_policy_sha256[:12]}:"
            f"sha256-{active_hash}"
        )


@dataclass(frozen=True)
class BatchShard:
    """One JSONL batch shard with its request count and byte size."""

    path: Path
    request_count: int
    byte_size: int


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
