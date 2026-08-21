"""Small, literal schema for one series-level resolution draft."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReviewModel(BaseModel):
    """Strict immutable base for model-facing review contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FileEpisode(ReviewModel):
    """One capture and the ordinary episode it should occupy."""

    capture_id: str = Field(min_length=1)
    episode: int = Field(gt=0)

    @field_validator("capture_id")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class KeepInCurrentSeries(ReviewModel):
    """Admit these captures under the AniList series Bronze already claims."""

    decision: Literal["keep_in_current_series"]
    files: tuple[FileEpisode, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class MoveToAnotherSeries(ReviewModel):
    """Admit these captures under one explicitly named destination series."""

    decision: Literal["move_to_another_series"]
    destination_anilist_id: int = Field(gt=0)
    files: tuple[FileEpisode, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class LeaveOutOfEpisodeIndex(ReviewModel):
    """Keep Bronze unchanged but emit no canonical episode for these captures."""

    decision: Literal["leave_out_of_episode_index"]
    capture_ids: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)


SeriesDecision = Annotated[
    KeepInCurrentSeries | MoveToAnotherSeries | LeaveOutOfEpisodeIndex,
    Field(discriminator="decision"),
]


class SeriesResolutionDraft(ReviewModel):
    """The complete proposed crosswalk for one Bronze-declared AniList series."""

    current_anilist_id: int = Field(gt=0)
    summary: str = Field(min_length=1)
    decisions: tuple[SeriesDecision, ...] = Field(min_length=1)
