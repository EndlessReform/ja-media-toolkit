"""Translation between domain materializations and Dagster events."""

from __future__ import annotations

from typing import Mapping

import dagster as dg
from dagster import AssetExecutionContext

from ja_media_data.products.lineage import MaterializationCatalog, structural_build_key
from ja_media_data.products.materialization import MaterializationContext
from ja_media_data.lakehouse.repository import DuckLakeRepository


def commit_context(
    context: AssetExecutionContext,
    *,
    target: str,
    recipe_revision: str,
    input_heads: Mapping[str, object],
) -> MaterializationContext:
    """Build recovery-grade lineage without writing executor checkpoints."""

    heads = dict(input_heads)
    run_id = context.run.run_id
    return MaterializationContext(
        attempt_id=f"dagster:{run_id}:{target}",
        pipeline_run_id=run_id,
        recipe_revision=recipe_revision,
        build_key=structural_build_key(target, recipe_revision, heads),
        input_heads=heads,
    )


def input_heads(
    repository: DuckLakeRepository, *targets: str
) -> dict[str, object]:
    """Read compact identities for declared domain dependencies."""

    return MaterializationCatalog(repository.connection).input_heads(*targets)


def event_metadata(
    repository: DuckLakeRepository,
    *,
    target: str,
    written: bool,
    rows: int,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Reference the durable domain head from a Dagster materialization."""

    head = MaterializationCatalog(repository.connection).current_head(target)
    metadata: dict[str, object] = {
        "domain_target": target,
        "rows": rows,
        "write_disposition": "written" if written else "reused",
    }
    if head is not None:
        metadata.update(
            {
                "materialization_id": head.materialization_id,
                "domain_fingerprint": head.fingerprint,
                "ducklake_snapshot_id": head.snapshot_id or 0,
            }
        )
    if extra:
        metadata.update(extra)
    return metadata


def output(
    *,
    output_name: str,
    fingerprint: str,
    metadata: Mapping[str, object],
) -> dg.MaterializeResult:
    """Emit one explicitly versioned output from a collection multi-asset."""

    return dg.MaterializeResult(
        asset_key=output_name,
        metadata=dict(metadata),
        data_version=dg.DataVersion(fingerprint),
    )
