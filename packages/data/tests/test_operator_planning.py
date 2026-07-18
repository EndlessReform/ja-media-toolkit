"""Pure proof that the O0 planner composes the required workload shapes."""

from __future__ import annotations

from ja_media_data.operator.planning import (
    CapabilityProfile,
    ExecutionIntent,
    Planner,
)
from ja_media_data.operator.products import (
    Currency,
    ProductKey,
    ProductObservation,
    Readiness,
    SubjectKey,
)
from ja_media_data.operator.registry import (
    ApprovalRequirement,
    CapabilityRequirement,
    DeferredFrontier,
    ExpansionContext,
    OperatorRegistry,
    RecipeSpec,
    ScopeSpec,
    StageExpansion,
    StageSpec,
    TargetSpec,
)


def test_fan_out_recipe_fork_and_shared_ancestry_are_generic() -> None:
    registry = OperatorRegistry()
    _stage(registry, "canonical", ("bronze",), "canonical", _inputs("bronze"))
    _stage(
        registry,
        "subtitle_candidates",
        ("canonical",),
        "subtitle",
        lambda output, context: StageExpansion(
            inputs=(context.product("canonical", output.subject, recipe_family="canon"),)
        ),
    )
    _stage(
        registry,
        "lid",
        ("subtitle",),
        "lid",
        lambda output, context: StageExpansion(
            inputs=(context.product("subtitle", output.subject, variant=output.variant),)
        ),
    )

    def comparison(output: ProductKey, context: ExpansionContext) -> StageExpansion:
        return StageExpansion(
            inputs=tuple(
                context.product(
                    "lid",
                    output.subject,
                    variant=stream,
                    recipe_family=recipe,
                )
                for stream in ("stream-1", "stream-2")
                for recipe in ("lid-a", "lid-b")
            )
        )

    _stage(registry, "lid_comparison", ("lid",), "lid_comparison", comparison)
    _recipe(registry, "canon-v1", "canon", "canonical", "canon@1")
    _recipe(registry, "lid-a-v1", "lid-a", "lid", "lid-a@1")
    _recipe(registry, "lid-b-v1", "lid-b", "lid", "lid-b@1")
    _target(registry, "lid-bakeoff", "lid_comparison")

    plan = Planner(registry).plan(_intent("lid-bakeoff", _episode("e1")), _sources("bronze"))

    assert [len(wave.items) for wave in plan.waves] == [1, 2, 4, 1]
    assert [item.stage for item in plan.work_items].count("canonical") == 1
    lid_outputs = [item.output for item in plan.work_items if item.stage == "lid"]
    assert {item.recipe_revision for item in lid_outputs} == {"lid-a@1", "lid-b@1"}
    assert {item.variant for item in lid_outputs} == {"stream-1", "stream-2"}


def test_join_selection_approval_capability_and_dataset_aggregation() -> None:
    registry = OperatorRegistry()
    cuda = CapabilityRequirement(name="cuda", minimum_vram_gb=16)
    _stage(
        registry,
        "align",
        ("audio", "cleaned_subtitle"),
        "alignment",
        lambda output, context: StageExpansion(
            inputs=(
                context.product("audio", output.subject),
                context.product("cleaned_subtitle", output.subject, variant=output.variant),
            )
        ),
        capabilities=(cuda,),
    )

    def select(output: ProductKey, context: ExpansionContext) -> StageExpansion:
        fingerprint = f"evidence:{output.subject.key}"
        return StageExpansion(
            inputs=tuple(
                context.product(
                    "alignment",
                    output.subject,
                    variant=candidate,
                    recipe_family="aligner",
                )
                for candidate in ("candidate-1", "candidate-2")
            ),
            approval=ApprovalRequirement(
                gate="timing-quality",
                subject=output.subject,
                input_fingerprint=fingerprint,
            ),
        )

    _stage(registry, "select_alignment", ("alignment",), "selected_alignment", select)
    _stage(
        registry,
        "series_report",
        ("selected_alignment",),
        "series_report",
        lambda output, context: StageExpansion(
            inputs=tuple(
                context.product("selected_alignment", _episode(f"{output.subject.key}:e{n}"))
                for n in (1, 2)
            )
        ),
    )
    _stage(
        registry,
        "eval_dataset",
        ("series_report",),
        "eval_dataset",
        lambda output, context: StageExpansion(
            inputs=tuple(
                context.product("series_report", SubjectKey(kind="series", key=series))
                for series in ("s1", "s2")
            )
        ),
    )
    _recipe(registry, "align-v1", "aligner", "align", "align@1")
    _target(registry, "timing-eval", "eval_dataset")
    intent = _intent("timing-eval", SubjectKey(kind="dataset", key="timing-v3"))

    blocked = Planner(registry).plan(
        intent, _sources("audio", "cleaned_subtitle")
    )
    assert len([item for item in blocked.work_items if item.stage == "align"]) == 8
    assert {item.readiness for item in blocked.work_items if item.stage == "align"} == {
        Readiness.BLOCKED_CAPABILITY
    }
    assert {item.readiness for item in blocked.work_items if item.stage == "select_alignment"} == {
        Readiness.BLOCKED_APPROVAL
    }
    approvals = frozenset(
        ("timing-quality", f"{series}:e{episode}", f"evidence:{series}:e{episode}")
        for series in ("s1", "s2")
        for episode in (1, 2)
    )
    runnable = Planner(registry).plan(
        intent,
        _sources("audio", "cleaned_subtitle"),
        capabilities=CapabilityProfile(names=frozenset({"cuda"}), vram_gb=24),
        approvals=approvals,
    )
    assert [len(wave.items) for wave in runnable.waves] == [8, 4, 2, 1]


def test_dynamic_map_reduce_replans_after_discovery_commit() -> None:
    registry = OperatorRegistry()
    discovery = {"chunks": ()}
    _stage(registry, "vad", ("audio",), "vad", _inputs("audio"))
    _stage(registry, "asr_chunk", ("vad",), "asr_chunk", _inputs("vad"))

    def merge(output: ProductKey, context: ExpansionContext) -> StageExpansion:
        if not discovery["chunks"]:
            return StageExpansion(
                inputs=(context.product("vad", output.subject),),
                frontier=DeferredFrontier(
                    stage="asr_chunk",
                    reason="VAD must commit before chunk subjects are known.",
                ),
            )
        return StageExpansion(
            inputs=tuple(
                context.product("asr_chunk", SubjectKey(kind="audio_chunk", key=chunk))
                for chunk in discovery["chunks"]
            )
        )

    _stage(registry, "merge_asr", ("asr_chunk",), "transcript", merge)
    _target(registry, "asr-transcript", "transcript")
    intent = _intent("asr-transcript", SubjectKey(kind="episode", key="s1:e1"))
    first = Planner(registry).plan(intent, _sources("audio"))
    assert [item.stage for item in first.work_items] == ["vad"]
    assert first.frontiers[0].stage == "asr_chunk"

    discovery["chunks"] = ("s1:e1:c1", "s1:e1:c2", "s1:e1:c3")
    second = Planner(registry).plan(intent, _sources("audio", "vad"))
    assert [len(wave.items) for wave in second.waves] == [3, 1]
    assert not second.frontiers


def _stage(
    registry: OperatorRegistry,
    name: str,
    inputs: tuple[str, ...],
    output: str,
    expand,
    *,
    capabilities: tuple[CapabilityRequirement, ...] = (),
) -> None:
    registry.add_stage(
        StageSpec(
            name=name,
            input_products=inputs,
            output_products=(output,),
            expand=expand,
            commit_granularity="product_instance",
            capabilities=capabilities,
        )
    )


def _recipe(
    registry: OperatorRegistry, recipe_id: str, family: str, stage: str, revision: str
) -> None:
    registry.add_recipe(
        RecipeSpec(
            recipe_id=recipe_id,
            family=family,
            name=recipe_id,
            revision=revision,
            stage=stage,
            output_products=registry.stages[stage].output_products,
            parameter_summary="fixture",
            source_path=__file__,
            tags=("default",),
        )
    )


def _target(registry: OperatorRegistry, name: str, product: str) -> None:
    registry.add_target(
        TargetSpec(
            name=name,
            product_type=product,
            description="fixture",
            expand=lambda scope, context: tuple(
                context.product(product, subject) for subject in scope.members
            ),
        )
    )


def _inputs(product: str):
    return lambda output, context: StageExpansion(
        inputs=(context.product(product, output.subject),)
    )


def _sources(*product_types: str):
    def observe(key: ProductKey):
        if key.product_type not in product_types:
            return None
        return ProductObservation(
            key=key, currency=Currency.CURRENT, readiness=Readiness.READY
        )

    return observe


def _intent(target: str, subject: SubjectKey) -> ExecutionIntent:
    return ExecutionIntent(
        target=target,
        scope=ScopeSpec(selector=subject.key, members=(subject,)),
    )


def _episode(key: str) -> SubjectKey:
    return SubjectKey(kind="episode", key=key)
