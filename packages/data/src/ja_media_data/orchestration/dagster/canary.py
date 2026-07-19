"""Non-publishing bounded canary job for resolver recipe evaluation."""

from __future__ import annotations

import hashlib
import json

import dagster as dg

from ja_media_data.orchestration.dagster.runtime import CanaryRuntime
from ja_media_data.products.episode_resolution.compiler import resolve_batch


@dg.op(
    required_resource_keys={"canary_runtime"},
    config_schema={"limit": dg.Field(int, default_value=100)},
)
def evaluate_resolution_canary(context) -> dict[str, object]:
    """Evaluate frozen source records without advancing any domain product head."""

    runtime: CanaryRuntime = context.resources.canary_runtime
    limit = int(context.op_config["limit"])
    if limit < 1:
        raise ValueError("canary limit must be positive")
    documents = runtime.selected_documents(limit)
    selected_ids = [item.marker.capture_id for item in documents]
    selection_fingerprint = hashlib.sha256(
        json.dumps(selected_ids, separators=(",", ":")).encode()
    ).hexdigest()
    result = resolve_batch(
        documents,
        store=runtime.store,
        metadata_provider=runtime.metadata_provider,
        repository=None,
        run_source=context.run.run_id,
    )
    proposed = sum(item.classification == "proposed" for item in result.results)
    quarantined = len(result.results) - proposed
    summary = {
        "scope": "canary",
        "publishes": False,
        "selected": len(documents),
        "selected_capture_ids": selected_ids,
        "selection_fingerprint": selection_fingerprint,
        "proposed": proposed,
        "quarantined": quarantined,
    }
    context.add_output_metadata(summary)
    return summary


@dg.job(
    name="resolution_canary",
    executor_def=dg.in_process_executor,
    tags={"ja_media/scope": "canary", "ja_media/publishes": "false"},
)
def resolution_canary_job() -> None:
    """Run bounded resolver evaluation without emitting asset materializations."""

    evaluate_resolution_canary()
