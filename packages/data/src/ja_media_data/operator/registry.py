"""Typed registries for compositional stages, recipes, and operator targets."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
from typing import Any

from pydantic import Field

from ja_media_data.operator.products import ContractModel, ProductKey, SubjectKey


class CapabilityRequirement(ContractModel):
    """A capability that must exist where a stage is dispatched."""

    name: str
    minimum_vram_gb: int | None = None
    external_service: str | None = None


class ApprovalRequirement(ContractModel):
    """A fingerprint-bound human gate required by a stage expansion."""

    gate: str
    subject: SubjectKey
    input_fingerprint: str


class DeferredFrontier(ContractModel):
    """Downstream work whose concrete product keys require an upstream commit."""

    stage: str
    reason: str
    estimated_cardinality: int | None = None


class StageExpansion(ContractModel):
    """Concrete inputs and barriers needed to produce one demanded output."""

    inputs: tuple[ProductKey, ...] = ()
    approval: ApprovalRequirement | None = None
    frontier: DeferredFrontier | None = None
    applicable: bool = True


class FingerprintInput(ContractModel):
    """One logical input plus the exact durable revision observed for planning."""

    key: ProductKey
    fingerprint: str | None = None


class RecipeSpec(ContractModel):
    """Named, content-addressed parameterization of one stage."""

    recipe_id: str
    family: str
    name: str
    revision: str
    stage: str
    output_products: tuple[str, ...]
    parameter_summary: str
    source_path: str
    capabilities: tuple[CapabilityRequirement, ...] = ()
    tags: tuple[str, ...] = ()


class ScopeSpec(ContractModel):
    """Resolved campaign membership used as the planner's stable scope."""

    selector: str
    members: tuple[SubjectKey, ...]


class CampaignSpec(ContractModel):
    """Saved operator intent; progress is deliberately absent."""

    campaign_id: str
    label: str
    note: str
    target: str
    scope: ScopeSpec
    recipe_bindings: dict[str, str] = Field(default_factory=dict)
    stop_target: str | None = None


@dataclass(frozen=True)
class ExpansionContext:
    """Recipe-aware helpers exposed to stage expansion functions."""

    registry: "OperatorRegistry"
    recipe_bindings: Mapping[str, str]

    def product(
        self,
        product_type: str,
        subject: SubjectKey,
        *,
        recipe_family: str | None = None,
        variant: str | None = None,
    ) -> ProductKey:
        revision = None
        if recipe_family:
            revision = self.registry.resolve_recipe(
                recipe_family, self.recipe_bindings
            ).revision
        return ProductKey(
            product_type=product_type,
            subject=subject,
            variant=variant,
            recipe_revision=revision,
        )


ExpansionFunction = Callable[[ProductKey, ExpansionContext], StageExpansion]
FingerprintFunction = Callable[[ProductKey, tuple[FingerprintInput, ...]], str]
TargetExpansion = Callable[[ScopeSpec, ExpansionContext], tuple[ProductKey, ...]]
Runner = Callable[..., Any]


def structural_fingerprint(
    output: ProductKey, inputs: tuple[FingerprintInput, ...]
) -> str:
    """Hash recipe-bearing output identity and observed upstream revisions."""

    payload = "\0".join(
        [
            output.stable_id,
            *(f"{item.key.stable_id}={item.fingerprint or 'missing'}" for item in inputs),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class StageSpec:
    """Executable structure supplied by a domain stage adapter."""

    name: str
    input_products: tuple[str, ...]
    output_products: tuple[str, ...]
    expand: ExpansionFunction
    commit_granularity: str
    implemented: bool = True
    capabilities: tuple[CapabilityRequirement, ...] = ()
    fingerprint: FingerprintFunction = structural_fingerprint
    runner: Runner | None = None


@dataclass(frozen=True)
class TargetSpec:
    """Operator-facing sink or pause point expanded into root demands."""

    name: str
    product_type: str
    description: str
    expand: TargetExpansion


@dataclass
class OperatorRegistry:
    """Single source for target introspection and planner producer lookup."""

    stages: dict[str, StageSpec] = field(default_factory=dict)
    recipes: dict[str, RecipeSpec] = field(default_factory=dict)
    targets: dict[str, TargetSpec] = field(default_factory=dict)

    def add_stage(self, spec: StageSpec) -> None:
        self._unique(self.stages, spec.name, "stage")
        self.stages[spec.name] = spec

    def add_recipe(self, spec: RecipeSpec) -> None:
        self._unique(self.recipes, spec.recipe_id, "recipe")
        if spec.stage not in self.stages:
            raise ValueError(f"recipe {spec.recipe_id} names unknown stage {spec.stage}")
        self.recipes[spec.recipe_id] = spec

    def add_target(self, spec: TargetSpec) -> None:
        self._unique(self.targets, spec.name, "target")
        self.targets[spec.name] = spec

    def resolve_recipe(
        self, family: str, bindings: Mapping[str, str]
    ) -> RecipeSpec:
        recipe_id = bindings.get(family)
        candidates = [item for item in self.recipes.values() if item.family == family]
        if recipe_id:
            recipe = self.recipes.get(recipe_id)
            if recipe is None or recipe.family != family:
                raise ValueError(f"invalid recipe binding {family}={recipe_id}")
            return recipe
        defaults = [item for item in candidates if "default" in item.tags]
        if len(defaults) != 1:
            raise ValueError(f"recipe family {family} requires an explicit binding")
        return defaults[0]

    def producer_for(self, key: ProductKey) -> StageSpec | None:
        candidates = [
            stage for stage in self.stages.values()
            if key.product_type in stage.output_products
        ]
        if key.recipe_revision:
            recipe_stages = {
                recipe.stage for recipe in self.recipes.values()
                if recipe.revision == key.recipe_revision
            }
            candidates = [item for item in candidates if item.name in recipe_stages]
        if len(candidates) > 1:
            raise ValueError(f"ambiguous producer for {key.stable_id}")
        return candidates[0] if candidates else None

    def target_dependencies(self, target_name: str) -> tuple[str, ...]:
        """Derive ordered executable ancestor targets from registered stages."""

        target = self.targets.get(target_name)
        if target is None:
            raise KeyError(target_name)
        product_targets = {item.product_type: item.name for item in self.targets.values()}
        ordered: list[str] = []
        visiting: set[str] = set()

        def visit(product_type: str) -> None:
            producers = [
                item for item in self.stages.values()
                if product_type in item.output_products
            ]
            if not producers:
                return
            if len(producers) > 1:
                raise ValueError(f"ambiguous producer family for {product_type}")
            stage = producers[0]
            if stage.name in visiting:
                raise ValueError(f"cycle detected at stage {stage.name}")
            visiting.add(stage.name)
            for dependency in stage.input_products:
                visit(dependency)
                dependency_target = product_targets.get(dependency)
                if dependency_target and dependency_target != target_name:
                    if dependency_target not in ordered:
                        ordered.append(dependency_target)
            visiting.remove(stage.name)

        visit(target.product_type)
        return tuple(ordered)

    @staticmethod
    def _unique(items: Mapping[str, object], key: str, kind: str) -> None:
        if key in items:
            raise ValueError(f"duplicate {kind}: {key}")
