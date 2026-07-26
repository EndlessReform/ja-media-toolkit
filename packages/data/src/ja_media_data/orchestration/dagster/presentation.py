"""Operator presentation metadata for Dagster computation nodes.

Edges never live here.  Campaign closure and ordering come from Dagster's
asset graph; this module only explains how a computation should be labelled
and which bounded domain tables provide its row counts.
"""

from dataclasses import dataclass

from ja_media_data.products.binding_acceptance.compiler import ACCEPTANCE_POLICY_VERSION
from ja_media_data.products.canonical_inputs.compiler import CANONICALIZATION_POLICY_VERSION
from ja_media_data.products.subtitle_lid.compiler import SUBTITLE_LID_RECIPE_VERSION


@dataclass(frozen=True)
class StagePresentation:
    """UI metadata for one Dagster asset computation node."""

    op_name: str
    stage: str
    label: str
    input_table: str
    output_table: str
    domain_target: str
    recipe_revision: str | None
    input_targets: tuple[str, ...] = ()
    uses_overrides: bool = False


PRESENTATIONS = (
    StagePresentation(
        "compile_episode_resolution", "episode_resolution", "Resolver proposals",
        "bronze_captures", "episode_binding_proposals", "episode_resolution", None,
    ),
    StagePresentation(
        "accepted_bindings_auto", "accepted_bindings", "Automatic acceptance",
        "episode_binding_proposals", "accepted_bindings_auto", "accepted_bindings",
        ACCEPTANCE_POLICY_VERSION, ("episode_resolution",),
    ),
    StagePresentation(
        "compile_canonical_inputs", "canonical_inputs", "Canonical inputs",
        "accepted_bindings_auto", "canonical_episode_inputs", "canonical_inputs",
        CANONICALIZATION_POLICY_VERSION,
        ("accepted_bindings", "bronze_captures"), True,
    ),
    StagePresentation(
        "subtitle_language_results", "subtitle_lid",
        "Subtitle language identification", "canonical_subtitle_inputs",
        "subtitle_language_results", "subtitle_lid", SUBTITLE_LID_RECIPE_VERSION,
        ("canonical_inputs",),
    ),
)

BY_OP = {item.op_name: item for item in PRESENTATIONS}
BY_STAGE = {item.stage: item for item in PRESENTATIONS}
