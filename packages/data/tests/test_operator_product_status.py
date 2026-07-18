"""Focused status and identity contract tests for optional future branches."""

from ja_media_data.operator.planning import ExecutionIntent, Planner
from ja_media_data.operator.products import (
    Currency,
    ProductKey,
    ProductObservation,
    Readiness,
    SubjectKey,
)
from ja_media_data.operator.registry import (
    OperatorRegistry,
    ScopeSpec,
    StageExpansion,
    StageSpec,
    TargetSpec,
)


def test_identity_distinguishes_variant_from_recipe_revision() -> None:
    subject = SubjectKey(kind="episode", key="series/episode-1")
    variant = ProductKey(product_type="result", subject=subject, variant="v1")
    recipe = ProductKey(product_type="result", subject=subject, recipe_revision="v1")

    assert variant.stable_id != recipe.stable_id
    assert "%2F" in variant.stable_id


def test_planner_separates_not_applicable_from_not_implemented() -> None:
    registry = OperatorRegistry()
    registry.add_stage(
        StageSpec(
            name="optional",
            input_products=(),
            output_products=("optional",),
            expand=lambda output, context: StageExpansion(applicable=False),
            commit_granularity="product_instance",
        )
    )
    registry.add_stage(
        StageSpec(
            name="future",
            input_products=(),
            output_products=("future",),
            expand=lambda output, context: StageExpansion(),
            commit_granularity="product_instance",
            implemented=False,
        )
    )
    for name in ("optional", "future"):
        registry.add_target(
            TargetSpec(
                name=name,
                product_type=name,
                description="fixture",
                expand=lambda scope, context, product=name: tuple(
                    context.product(product, subject) for subject in scope.members
                ),
            )
        )
    scope = ScopeSpec(
        selector="episode:e1", members=(SubjectKey(kind="episode", key="e1"),)
    )

    optional = Planner(registry).plan(
        ExecutionIntent(target="optional", scope=scope), lambda key: None
    )
    future = Planner(registry).plan(
        ExecutionIntent(target="future", scope=scope), lambda key: None
    )

    assert len(optional.not_applicable) == 1
    assert not optional.not_implemented
    assert len(future.not_implemented) == 1
    assert not future.not_applicable


def test_desired_fingerprint_changes_with_observed_input_revision() -> None:
    registry = OperatorRegistry()
    registry.add_stage(
        StageSpec(
            name="compile",
            input_products=("source",),
            output_products=("result",),
            expand=lambda output, context: StageExpansion(
                inputs=(context.product("source", output.subject),)
            ),
            commit_granularity="product_instance",
        )
    )
    registry.add_target(
        TargetSpec(
            name="result",
            product_type="result",
            description="fixture",
            expand=lambda scope, context: tuple(
                context.product("result", subject) for subject in scope.members
            ),
        )
    )
    subject = SubjectKey(kind="episode", key="e1")
    intent = ExecutionIntent(
        target="result", scope=ScopeSpec(selector="e1", members=(subject,))
    )

    def plan_for(revision: str):
        def observe(key: ProductKey):
            if key.product_type != "source":
                return None
            return ProductObservation(
                key=key,
                currency=Currency.CURRENT,
                readiness=Readiness.READY,
                fingerprint=revision,
            )

        return Planner(registry).plan(intent, observe)

    assert (
        plan_for("source-v1").work_items[0].desired_fingerprint
        != plan_for("source-v2").work_items[0].desired_fingerprint
    )
