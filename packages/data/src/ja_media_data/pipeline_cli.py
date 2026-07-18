"""Developer CLI presentation for the small Phase D target registry."""

from __future__ import annotations

from dataclasses import asdict
import json

from ja_media_data.bronze_store import bronze_store_from_env
from ja_media_data.lakehouse.repository import repository_from_env
from ja_media_data.operator.phase_d_registry import build_phase_d_registry
from ja_media_data.phase_d import binding_override_revision
from ja_media_data.pipeline_repository import PipelineRepository


def print_targets() -> None:
    """Print stable target names and their ordered dependency closure."""

    registry = build_phase_d_registry()
    targets = {
        name: registry.target_dependencies(name) for name in registry.targets
    }
    print(json.dumps(targets, indent=2, sort_keys=True))


def run_target(target: str, *, force_from: str | None = None) -> None:
    """Run one target and its dependencies, reporting every stage outcome."""

    store = bronze_store_from_env()
    registry = build_phase_d_registry()
    if target not in registry.targets:
        raise SystemExit(f"unknown target: {target}")
    ordered_targets = (*registry.target_dependencies(target), target)
    with repository_from_env() as repository:
        pipeline = PipelineRepository(repository.connection)
        run_id = pipeline.start_pipeline_run(
            target, forced_from_stage=force_from,
            override_revision=binding_override_revision(repository),
        )
        results = []
        forcing = False
        try:
            for ordinal, target_name in enumerate(ordered_targets, start=1):
                target_spec = registry.targets[target_name]
                producer = next(
                    stage for stage in registry.stages.values()
                    if target_spec.product_type in stage.output_products
                )
                if producer.runner is None:
                    raise SystemExit(f"target is not implemented: {target_name}")
                forcing = forcing or producer.name == force_from
                results.append(producer.runner(
                    repository, store, pipeline_run_id=run_id, ordinal=ordinal,
                    force=forcing,
                ))
        except Exception as error:
            pipeline.finish_pipeline_run(run_id, error=error)
            raise
        pipeline.finish_pipeline_run(run_id)
    print(json.dumps([asdict(item) for item in results], indent=2, sort_keys=True))
