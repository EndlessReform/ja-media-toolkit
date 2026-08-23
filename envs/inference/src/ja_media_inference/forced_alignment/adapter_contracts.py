"""HTTP request and response models for the colocated Qwen adapter spike."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AudioCacheRequest(_Contract):
    """Pinned Bronze audio object to cache beside vLLM."""

    object_bucket: str = Field(min_length=1)
    object_key: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    codec: str = Field(pattern=r"^[a-z0-9]{1,8}$")


class AudioCacheResponse(_Contract):
    audio_id: str
    cached: bool
    byte_count: int


class AlignmentTokenRequest(_Contract):
    """One already-segmented Japanese token sent to the model adapter."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    group_index: int = Field(ge=0)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_character_range(self) -> AlignmentTokenRequest:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("character ranges require both start and end")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("character range end precedes start")
        return self


class AlignmentRequest(_Contract):
    """One source-clock crop and its ordered alignment tokens."""

    audio_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    crop_start_s: float = Field(ge=0)
    crop_end_s: float = Field(gt=0)
    tokens: list[AlignmentTokenRequest] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_crop(self) -> AlignmentRequest:
        if self.crop_end_s <= self.crop_start_s:
            raise ValueError("crop end must follow crop start")
        if self.crop_end_s - self.crop_start_s > 300:
            raise ValueError("crop duration exceeds Qwen's 300-second limit")
        if len({token.id for token in self.tokens}) != len(self.tokens):
            raise ValueError("alignment token IDs must be unique")
        return self


class TokenAlignmentResponse(_Contract):
    token_id: str
    start_s: float
    end_s: float
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlignmentResponse(_Contract):
    audio_id: str
    crop_start_s: float
    crop_end_s: float
    alignments: list[TokenAlignmentResponse]


class HealthResponse(_Contract):
    status: str
    vllm_base_url: str
    audio_cache_dir: str
