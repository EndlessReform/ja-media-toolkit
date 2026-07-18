"""Backward demand planner over the static operator registry."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import Field

from ja_media_data.operator.products import (
    Applicability,
    Currency,
    ProductKey,
    ProductObservation,
    Readiness,
)
from ja_media_data.operator.registry import (
    ApprovalRequirement,
    CapabilityRequirement,
    DeferredFrontier,
    ExpansionContext,
    FingerprintInput,
    OperatorRegistry,
    ScopeSpec,
)
from ja_media_data.operator.products import ContractModel


class ExecutionIntent(ContractModel):
    """Immutable request to plan one target for a resolved scope."""

    target: str
    scope: ScopeSpec
    recipe_bindings: dict[str, str] = Field(default_factory=dict)
    stop_target: str | None = None


class CapabilityProfile(ContractModel):
    """Capabilities currently available to the planning process."""

    names: frozenset[str] = frozenset()
    vram_gb: int | None = None
    services: frozenset[str] = frozenset()

    def satisfies(self, requirement: CapabilityRequirement) -> bool:
        if requirement.name not in self.names:
            return False
        if requirement.minimum_vram_gb is not None and (
            self.vram_gb is None or self.vram_gb < requirement.minimum_vram_gb
        ):
            return False
        return not (
            requirement.external_service
            and requirement.external_service not in self.services
        )


class WorkItem(ContractModel):
    """One demanded product and the exact inputs needed to produce it."""

    stage: str
    output: ProductKey
    inputs: tuple[ProductKey, ...]
    desired_fingerprint: str
    commit_granularity: str
    readiness: Readiness
    blocking_reasons: tuple[str, ...] = ()
    approval: ApprovalRequirement | None = None
    capabilities: tuple[CapabilityRequirement, ...] = ()


class ExecutionWave(ContractModel):
    """Set of independently runnable work after earlier waves commit."""

    number: int
    items: tuple[ProductKey, ...]


class ExecutionPlan(ContractModel):
    """Stable JSON plan; executing or re-planning it is a separate operation."""

    intent: ExecutionIntent
    roots: tuple[ProductKey, ...]
    reused: tuple[ProductKey, ...]
    not_applicable: tuple[ProductKey, ...]
    not_implemented: tuple[ProductKey, ...]
    work_items: tuple[WorkItem, ...]
    waves: tuple[ExecutionWave, ...]
    frontiers: tuple[DeferredFrontier, ...]
    unresolved: tuple[ProductKey, ...]


ObservationLoader = Callable[[ProductKey], ProductObservation | None]


class Planner:
    """Generic graph walker with no knowledge of media workload shapes."""

    def __init__(self, registry: OperatorRegistry) -> None:
        self.registry = registry

    def plan(
        self,
        intent: ExecutionIntent,
        observations: ObservationLoader,
        *,
        capabilities: CapabilityProfile | None = None,
        approvals: frozenset[tuple[str, str, str]] = frozenset(),
    ) -> ExecutionPlan:
        target = self.registry.targets.get(intent.target)
        if target is None:
            raise KeyError(intent.target)
        context = ExpansionContext(self.registry, intent.recipe_bindings)
        planning_target = target
        if intent.stop_target and intent.stop_target in self.registry.targets:
            planning_target = self.registry.targets[intent.stop_target]
        roots = planning_target.expand(intent.scope, context)
        reused: dict[str, ProductKey] = {}
        not_applicable: dict[str, ProductKey] = {}
        not_implemented: dict[str, ProductKey] = {}
        unresolved: dict[str, ProductKey] = {}
        items: dict[str, WorkItem] = {}
        frontiers: dict[tuple[str, str], DeferredFrontier] = {}
        visiting: set[str] = set()
        profile = capabilities or CapabilityProfile()
        observation_cache: dict[str, ProductObservation | None] = {}

        def observe(key: ProductKey) -> ProductObservation | None:
            identity = key.stable_id
            if identity not in observation_cache:
                observation_cache[identity] = observations(key)
            return observation_cache[identity]

        def fingerprint_input(key: ProductKey) -> FingerprintInput:
            observation = observe(key)
            return FingerprintInput(
                key=key,
                fingerprint=observation.fingerprint if observation else None,
            )

        def visit(key: ProductKey) -> None:
            identity = key.stable_id
            if identity in (
                reused | not_applicable | not_implemented | unresolved | items
            ):
                return
            if identity in visiting:
                raise ValueError(f"cycle detected at {identity}")
            observation = observe(key)
            if observation and observation.applicability == Applicability.NOT_APPLICABLE:
                not_applicable[identity] = key
                return
            if observation and observation.currency == Currency.CURRENT:
                reused[identity] = key
                return
            producer = self.registry.producer_for(key)
            if producer is None:
                unresolved[identity] = key
                return
            if not producer.implemented:
                not_implemented[identity] = key
                return
            visiting.add(identity)
            expansion = producer.expand(key, context)
            if not expansion.applicable:
                not_applicable[identity] = key
                visiting.remove(identity)
                return
            for input_key in expansion.inputs:
                visit(input_key)
            if expansion.frontier:
                frontier_key = (expansion.frontier.stage, expansion.frontier.reason)
                frontiers[frontier_key] = expansion.frontier
                visiting.remove(identity)
                return
            reasons = []
            approval_missing = bool(expansion.approval and (
                expansion.approval.gate,
                expansion.approval.subject.key,
                expansion.approval.input_fingerprint,
            ) not in approvals)
            if approval_missing and expansion.approval:
                reasons.append(f"approval:{expansion.approval.gate}")
            missing_capabilities = [
                item for item in producer.capabilities if not profile.satisfies(item)
            ]
            reasons.extend(f"capability:{item.name}" for item in missing_capabilities)
            readiness = (
                Readiness.BLOCKED_APPROVAL if approval_missing
                else Readiness.BLOCKED_CAPABILITY if missing_capabilities
                else Readiness.READY
            )
            items[identity] = WorkItem(
                stage=producer.name,
                output=key,
                inputs=expansion.inputs,
                desired_fingerprint=producer.fingerprint(
                    key,
                    tuple(fingerprint_input(input_key) for input_key in expansion.inputs),
                ),
                commit_granularity=producer.commit_granularity,
                readiness=readiness,
                blocking_reasons=tuple(reasons),
                approval=expansion.approval,
                capabilities=producer.capabilities,
            )
            visiting.remove(identity)

        for root in roots:
            visit(root)
        satisfied = reused | not_applicable
        normalized, waves = _schedule(items, satisfied, unresolved | not_implemented)
        return ExecutionPlan(
            intent=intent,
            roots=roots,
            reused=tuple(reused.values()),
            not_applicable=tuple(not_applicable.values()),
            not_implemented=tuple(not_implemented.values()),
            work_items=tuple(normalized.values()),
            waves=waves,
            frontiers=tuple(frontiers.values()),
            unresolved=tuple(unresolved.values()),
        )


def _schedule(
    items: dict[str, WorkItem],
    reused: dict[str, ProductKey],
    unresolved: dict[str, ProductKey],
) -> tuple[dict[str, WorkItem], tuple[ExecutionWave, ...]]:
    """Topologically group runnable items and truthfully mark blocked parents."""

    remaining = set(items)
    completed = set(reused)
    waves = []
    while remaining:
        eligible = sorted(
            identity for identity in remaining
            if items[identity].readiness == Readiness.READY
            and all(
                dependency.stable_id in completed
                for dependency in items[identity].inputs
            )
        )
        if not eligible:
            break
        waves.append(
            ExecutionWave(
                number=len(waves) + 1,
                items=tuple(items[identity].output for identity in eligible),
            )
        )
        remaining.difference_update(eligible)
        completed.update(eligible)
    normalized = dict(items)
    for identity in remaining:
        item = items[identity]
        if item.readiness != Readiness.READY:
            continue
        blockers = tuple(
            dependency.stable_id for dependency in item.inputs
            if dependency.stable_id in remaining or dependency.stable_id in unresolved
        )
        normalized[identity] = item.model_copy(
            update={
                "readiness": Readiness.BLOCKED_INPUTS,
                "blocking_reasons": blockers,
            }
        )
    return normalized, tuple(waves)
