"""Declared storage and dependency contracts for the real Phase D spine."""

from __future__ import annotations

from dataclasses import dataclass

from ja_media_data.phase_d import (
    ACCEPTANCE_POLICY_VERSION,
    CANONICALIZATION_POLICY_VERSION,
    SUBTITLE_LID_RECIPE_VERSION,
)


@dataclass(frozen=True)
class StageContract:
    """The facts needed to observe currency without loading product rows."""

    stage: str
    label: str
    inputs: tuple[str, ...]
    input_table: str
    output_table: str
    recipe_revision: str | None
    uses_overrides: bool = False


STAGE_CONTRACTS = (
    StageContract(
        "episode_resolution", "Resolver proposals", (),
        "bronze_captures", "episode_binding_proposals", None,
    ),
    StageContract(
        "accepted_bindings", "Automatic acceptance", ("episode_resolution",),
        "episode_binding_proposals", "accepted_bindings_auto",
        ACCEPTANCE_POLICY_VERSION,
    ),
    StageContract(
        "canonical_inputs", "Canonical inputs",
        ("accepted_bindings", "bronze_captures"),
        "accepted_bindings_auto", "canonical_episode_inputs",
        CANONICALIZATION_POLICY_VERSION, True,
    ),
    StageContract(
        "subtitle_lid", "Subtitle language identification", ("canonical_inputs",),
        "canonical_subtitle_inputs", "subtitle_language_results",
        SUBTITLE_LID_RECIPE_VERSION,
    ),
)

STAGE_BY_NAME = {item.stage: item for item in STAGE_CONTRACTS}
CANONICALIZATION_SPINE = STAGE_CONTRACTS[:3]
