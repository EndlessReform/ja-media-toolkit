"""Collection-product currency derived from product-owned lineage."""

from __future__ import annotations

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.orchestration.dagster.presentation import StagePresentation
from ja_media_data.products.canonical_inputs.compiler import binding_override_revision
from ja_media_data.products.lineage import (
    MaterializationCatalog,
    ProductHead,
    structural_build_key,
)


def evaluate_currency(
    repository: DuckLakeRepository,
    presentation: StagePresentation,
    head: ProductHead | None,
    *,
    snapshot_id: int | None = None,
    override_revision: int | None = None,
) -> tuple[str, str | None]:
    """Compare one domain head with its declared current product inputs."""

    if head is None:
        return "missing", "no committed materialization"
    if presentation.recipe_revision is None:
        return "current", None
    if head.recipe_revision != presentation.recipe_revision:
        return "stale_recipe", "committed recipe differs from the asset recipe"
    catalog = MaterializationCatalog(repository.connection)
    inputs = catalog.input_heads(
        *presentation.input_targets, snapshot_id=snapshot_id
    )
    if presentation.uses_overrides:
        revision = (
            override_revision if override_revision is not None
            else binding_override_revision(repository)
        )
        inputs["binding_overrides"] = {"revision": revision}
    desired = structural_build_key(
        presentation.domain_target, presentation.recipe_revision, inputs
    )
    if head.build_key == desired:
        return "current", None
    if presentation.uses_overrides:
        stored = _revision(head.input_heads.get("binding_overrides"))
        current = _revision(inputs.get("binding_overrides"))
        if stored != current:
            return "stale_override", (
                f"override revision advanced from {stored} to {current}"
            )
    return "stale_input", "one or more upstream product heads changed"


def _revision(value: object) -> int | None:
    if isinstance(value, dict) and value.get("revision") is not None:
        return int(value["revision"])
    return None
