"""Non-publishing bounded canary job for resolver recipe evaluation."""

from __future__ import annotations

from collections import Counter
import hashlib
import json

import dagster as dg

from ja_media_data.orchestration.dagster.runtime import CanaryRuntime
from ja_media_data.products.episode_resolution.compiler import (
    ResolutionResult,
    resolve_batch,
)


_QUARANTINE_EXAMPLE_LIMIT = 5


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
    quarantines = [item for item in result.results if item.classification != "proposed"]
    summary = {
        "scope": "canary",
        "publishes": False,
        "selected": len(documents),
        "selected_capture_ids": selected_ids,
        "selection_fingerprint": selection_fingerprint,
        "proposed": proposed,
        "quarantined": quarantined,
        "quarantine_reasons": dict(
            sorted(Counter(item.reason for item in quarantines).items())
        ),
        "quarantine_examples": _bounded_quarantine_examples(quarantines),
    }
    context.add_output_metadata(summary)
    return summary


def _quarantine_example(item: ResolutionResult) -> dict[str, object]:
    """Expose the compared values instead of only naming a failed invariant."""

    resolution_context = item.resolution_context
    return {
        "capture_id": item.capture_id,
        "series_id": item.series_id,
        "stem": item.stem,
        "reason": item.reason,
        "issue_kind": item.issue_kind,
        "manifest_key": resolution_context.get("manifest_key"),
        "parsed_filename_title": resolution_context.get("ptn_title"),
        "parser_episode": resolution_context.get("ptn_ordinary_episode"),
        "explicit_episode_numbers": resolution_context.get(
            "explicit_episode_tokens"
        ),
        "episode_ranges": resolution_context.get("episode_ranges"),
        "declared_series": resolution_context.get("series"),
        "declared_series_metadata": resolution_context.get("metadata"),
        "error": resolution_context.get("error"),
    }


def _bounded_quarantine_examples(
    quarantines: list[ResolutionResult],
) -> list[dict[str, object]]:
    """Prefer reason coverage before filling the bounded diagnostic sample."""

    selected: list[ResolutionResult] = []
    selected_capture_ids: set[str] = set()
    represented_reasons: set[str] = set()
    for item in quarantines:
        if item.reason in represented_reasons:
            continue
        selected.append(item)
        selected_capture_ids.add(item.capture_id)
        represented_reasons.add(item.reason)
        if len(selected) == _QUARANTINE_EXAMPLE_LIMIT:
            break
    for item in quarantines:
        if len(selected) == _QUARANTINE_EXAMPLE_LIMIT:
            break
        if item.capture_id not in selected_capture_ids:
            selected.append(item)
    return [_quarantine_example(item) for item in selected]


@dg.job(
    name="resolution_canary",
    executor_def=dg.in_process_executor,
    tags={"ja_media/scope": "canary", "ja_media/publishes": "false"},
)
def resolution_canary_job() -> None:
    """Run bounded resolver evaluation without emitting asset materializations."""

    evaluate_resolution_canary()
