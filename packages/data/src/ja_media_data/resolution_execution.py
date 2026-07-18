"""Execution-checkpoint wrapper for an applied resolver batch.

The resolver itself remains a deterministic compiler.  This module owns the
operator-facing run/checkpoint bookkeeping needed when that compiler is
dispatched from a CLI or another manual execution surface.
"""

from __future__ import annotations

from collections.abc import Sequence

from ja_media_data.bronze_store import BronzeDocument, BronzeStore
from ja_media_data.episode_metadata import EpisodeMetadataProvider
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.pipeline_repository import PipelineRepository
from ja_media_data.resolution_service import ResolutionBatchResult, resolve_batch


def apply_resolution_batch(
    documents: Sequence[BronzeDocument],
    *,
    store: BronzeStore,
    metadata_provider: EpisodeMetadataProvider,
    repository: DuckLakeRepository,
) -> tuple[ResolutionBatchResult, int]:
    """Compile one resolver run and record its independently atomic checkpoint."""

    pipeline = PipelineRepository(repository.connection)
    run_id = pipeline.start_pipeline_run(
        "episode_resolution", forced_from_stage=None, override_revision=0
    )
    execution = pipeline.begin_stage(
        run_id,
        "episode_resolution",
        1,
        "episode-resolver-v1",
        {"bronze_scan": {"documents": len(documents)}},
    )
    try:
        batch = resolve_batch(
            documents,
            store=store,
            metadata_provider=metadata_provider,
            repository=repository,
            run_source=execution.attempt_id,
        )
    except Exception as error:
        pipeline.finish_stage(execution, disposition="failed", error=error)
        pipeline.finish_pipeline_run(run_id, error=error)
        raise

    head = pipeline.current_head("episode_resolution")
    pipeline.finish_stage(
        execution,
        disposition=(
            "succeeded"
            if batch.resolution_write and batch.resolution_write.written
            else "reused"
        ),
        materialization_id=head.materialization_id if head else None,
    )
    pipeline.finish_pipeline_run(run_id)

    # A product commit and its out-of-line object flush have a narrow crash
    # boundary.  Flushing on every applied run makes a no-op retry repair it.
    return batch, repository.flush_inlined_data()
