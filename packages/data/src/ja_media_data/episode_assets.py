"""Dagster execution and validation surfaces for stable episode resolution."""

from functools import lru_cache

import dagster as dg
from dagster import AssetCheckExecutionContext, AssetExecutionContext

from ja_media_data.bronze import (
    BRONZE_CAPTURE_PARTITIONS,
    bronze_capture,
    bronze_store_from_env,
    ledger_repository_from_env,
)
from ja_media_data.episode_metadata import AniListEpisodeMetadataProvider
from ja_media_data.resolution_service import resolve_indexed_capture


@lru_cache(maxsize=1)
def episode_metadata_provider() -> AniListEpisodeMetadataProvider:
    """Reuse exact-ID AniList metadata within a code-location process."""

    return AniListEpisodeMetadataProvider()


@dg.asset(
    name="episode_resolution",
    deps=[bronze_capture],
    partitions_def=BRONZE_CAPTURE_PARTITIONS,
    group_name="silver",
    kinds={"python", "postgresql"},
    description=(
        "Versioned filename and AniList-bound resolution for one bronze capture; "
        "ambiguous outcomes are durable review issues, not failed compute."
    ),
)
def episode_resolution(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Resolve one capture partition and commit its durable ledger outcome."""

    result = resolve_indexed_capture(
        context.partition_key,
        store=bronze_store_from_env(),
        metadata_provider=episode_metadata_provider(),
        repository=ledger_repository_from_env(),
        dagster_run_id=context.run_id,
    )
    return dg.MaterializeResult(
        metadata={
            "capture_id": result.capture_id,
            "series_id": result.series_id,
            "classification": result.classification,
            "reason": result.reason,
            "locator": result.locator or "",
            "stem": result.stem,
        }
    )


@dg.asset_check(
    asset=episode_resolution,
    name="stable_episode_mapping",
    blocking=False,
    description=(
        "Passes only when the capture has one accepted current locator; open "
        "ambiguity or overlap blocks downstream silver assets."
    ),
)
def stable_episode_mapping(
    context: AssetCheckExecutionContext,
) -> dg.AssetCheckResult:
    """Expose accepted-versus-quarantined state in Dagster's checks UI."""

    repository = ledger_repository_from_env()
    binding = repository.get_current_binding_for_capture(context.partition_key)
    if binding is not None:
        locator = f"{binding.namespace}:{binding.series_id}:{binding.episode}"
        return dg.AssetCheckResult(
            passed=True,
            metadata={"binding_id": binding.binding_id, "locator": locator},
        )
    issue = repository.get_latest_open_issue(context.partition_key)
    metadata = (
        {
            "issue_id": issue.issue_id,
            "kind": issue.kind,
            "reason": str(issue.details.get("reason", "unknown")),
        }
        if issue is not None
        else {"reason": "no binding or open issue was found"}
    )
    return dg.AssetCheckResult(passed=False, metadata=metadata)


@dg.asset(
    name="validated_episode_mapping",
    deps=[episode_resolution],
    partitions_def=BRONZE_CAPTURE_PARTITIONS,
    group_name="silver",
    kinds={"postgresql"},
    output_required=False,
    description=(
        "Conditional downstream boundary: materializes only when the capture "
        "has one accepted current episode locator."
    ),
)
def validated_episode_mapping(
    context: AssetExecutionContext,
):
    """Yield no output for quarantine while keeping the Dagster run successful."""

    binding = ledger_repository_from_env().get_current_binding_for_capture(
        context.partition_key
    )
    if binding is None:
        context.log.info("Capture is quarantined; no validated mapping produced")
        return
    yield dg.MaterializeResult(
        metadata={
            "binding_id": binding.binding_id,
            "locator": f"{binding.namespace}:{binding.series_id}:{binding.episode}",
            "capture_id": binding.audio_capture_id,
        }
    )
