"""Real Phase D registry entries and runners for the first operator slice."""

from __future__ import annotations

from ja_media_data.operator.products import ProductKey, SubjectKey
from ja_media_data.operator.registry import (
    ExpansionContext,
    OperatorRegistry,
    RecipeSpec,
    ScopeSpec,
    StageExpansion,
    StageSpec,
    TargetSpec,
)
from ja_media_data.phase_d import (
    ACCEPTANCE_POLICY_VERSION,
    CANONICALIZATION_POLICY_VERSION,
    SUBTITLE_LID_RECIPE_VERSION,
    compile_acceptances,
    compile_canonical_inputs,
    compile_subtitle_lid,
)


CORPUS = SubjectKey(kind="corpus", key="all")


def build_phase_d_registry() -> OperatorRegistry:
    """Build only the implemented Phase D vocabulary, with no empty future nouns."""

    registry = OperatorRegistry()
    registry.add_stage(
        StageSpec(
            name="accepted_bindings",
            input_products=("episode_binding_proposals",),
            output_products=("accepted_bindings",),
            expand=_accepted_inputs,
            commit_granularity="corpus",
            runner=lambda repository, store, **options: compile_acceptances(
                repository, **options
            ),
        )
    )
    registry.add_stage(
        StageSpec(
            name="canonical_inputs",
            input_products=("accepted_bindings", "bronze_captures"),
            output_products=("canonical_inputs",),
            expand=_canonical_inputs,
            commit_granularity="corpus",
            runner=compile_canonical_inputs,
        )
    )
    registry.add_stage(
        StageSpec(
            name="subtitle_lid",
            input_products=("canonical_inputs",),
            output_products=("subtitle_lid",),
            expand=_lid_inputs,
            commit_granularity="corpus",
            runner=compile_subtitle_lid,
        )
    )
    for recipe in _recipes():
        registry.add_recipe(recipe)
    for name, product_type, description, recipe_family in (
        (
            "accepted-bindings",
            "accepted_bindings",
            "Apply the automatic proposal-admission policy.",
            "binding-acceptance",
        ),
        (
            "canonical-inputs",
            "canonical_inputs",
            "Select canonical episode and subtitle inputs.",
            "canonicalization",
        ),
        (
            "subtitle-lid",
            "subtitle_lid",
            "Classify the canonical subtitle inputs.",
            "subtitle-lid",
        ),
    ):
        registry.add_target(
            TargetSpec(
                name=name,
                product_type=product_type,
                description=description,
                expand=_target_expander(product_type, recipe_family),
            )
        )
    return registry


def _recipes() -> tuple[RecipeSpec, ...]:
    return (
        RecipeSpec(
            recipe_id="accept-resolver-proposals-v1",
            family="binding-acceptance",
            name="Accept resolver proposals",
            revision=ACCEPTANCE_POLICY_VERSION,
            stage="accepted_bindings",
            output_products=("accepted_bindings",),
            parameter_summary="Admit every resolver proposal.",
            source_path="packages/data/src/ja_media_data/phase_d.py",
            tags=("default",),
        ),
        RecipeSpec(
            recipe_id="latest-manifest-modified-v1",
            family="canonicalization",
            name="Latest admitted capture",
            revision=CANONICALIZATION_POLICY_VERSION,
            stage="canonical_inputs",
            output_products=("canonical_inputs",),
            parameter_summary="Apply override, otherwise select latest admitted capture.",
            source_path="packages/data/src/ja_media_data/phase_d.py",
            tags=("default",),
        ),
        RecipeSpec(
            recipe_id="subtitle-script-fasttext-v1",
            family="subtitle-lid",
            name="Script plus fastText subtitle LID",
            revision=SUBTITLE_LID_RECIPE_VERSION,
            stage="subtitle_lid",
            output_products=("subtitle_lid",),
            parameter_summary="Script heuristics with sampled line-language fallback.",
            source_path="packages/data/src/ja_media_data/phase_d.py",
            tags=("default",),
        ),
    )


def _target_expander(product_type: str, family: str):
    def expand(scope: ScopeSpec, context: ExpansionContext) -> tuple[ProductKey, ...]:
        return tuple(
            context.product(product_type, subject, recipe_family=family)
            for subject in scope.members
        )

    return expand


def _accepted_inputs(
    output: ProductKey, context: ExpansionContext
) -> StageExpansion:
    return StageExpansion(
        inputs=(context.product("episode_binding_proposals", output.subject),)
    )


def _canonical_inputs(
    output: ProductKey, context: ExpansionContext
) -> StageExpansion:
    return StageExpansion(
        inputs=(
            context.product(
                "accepted_bindings",
                output.subject,
                recipe_family="binding-acceptance",
            ),
            context.product("bronze_captures", output.subject),
        )
    )


def _lid_inputs(output: ProductKey, context: ExpansionContext) -> StageExpansion:
    return StageExpansion(
        inputs=(
            context.product(
                "canonical_inputs",
                output.subject,
                recipe_family="canonicalization",
            ),
        )
    )
