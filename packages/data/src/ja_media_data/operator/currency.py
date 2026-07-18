"""Server-side currency evaluation from declared dependency heads."""

from __future__ import annotations

from ja_media_data.execution import ProductHead, structural_build_key
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.stage_contracts import StageContract
from ja_media_data.phase_d import binding_override_revision
from ja_media_data.pipeline_repository import PipelineRepository


def evaluate_currency(
    repository: DuckLakeRepository, contract: StageContract, head: ProductHead | None,
    *, snapshot_id: int | None = None, override_revision: int | None = None,
) -> tuple[str, str | None]:
    """Return truthful product currency independently of execution status."""

    if head is None:
        return "missing", "no committed materialization"
    if contract.recipe_revision is None:
        return "current", None
    if head.recipe_revision != contract.recipe_revision:
        return "stale_recipe", "committed recipe differs from the registered recipe"
    pipeline = PipelineRepository(repository.connection)
    inputs = pipeline.input_heads(*contract.inputs, snapshot_id=snapshot_id)
    if contract.uses_overrides:
        revision = (
            override_revision
            if override_revision is not None
            else binding_override_revision(repository)
        )
        inputs["binding_overrides"] = {"revision": revision}
    desired = structural_build_key(contract.stage, contract.recipe_revision, inputs)
    if head.build_key == desired:
        return "current", None
    if contract.uses_overrides:
        stored = _revision(head, "binding_overrides")
        current = _revision_value(inputs.get("binding_overrides"))
        if stored != current:
            return "stale_override", f"override revision advanced from {stored} to {current}"
    return "stale_input", "one or more upstream product heads changed"


def _revision(head: ProductHead, name: str) -> int | None:
    return _revision_value(head.input_heads.get(name))


def _revision_value(value: object) -> int | None:
    if isinstance(value, dict) and value.get("revision") is not None:
        return int(value["revision"])
    return None
