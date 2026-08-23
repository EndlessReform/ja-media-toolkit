"""Execute one prepared cue window through local or colocated Qwen clients."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

from ja_media_inference.forced_alignment.alignment_scores import summarize_token_scores
from ja_media_inference.forced_alignment.qwen3_adapter_client import (
    Qwen3AdapterClient,
)
from ja_media_inference.forced_alignment.qwen3_vllm import Qwen3VllmForcedAligner
from ja_media_inference.forced_alignment.text_units import (
    AlignmentTextGroup,
    AlignmentToken,
    TokenAlignment,
    merge_token_alignments_by_group,
    segment_group_with_nagisa,
)


@dataclass(frozen=True)
class PreparedAlignmentWindow:
    """Caller-owned cues and tokens prepared before concurrent HTTP execution."""

    records: list[dict[str, Any]]
    groups: list[AlignmentTextGroup]
    tokens: list[AlignmentToken]
    text_field: str


def align_local_window(
    aligner: Qwen3VllmForcedAligner,
    audio_path: Path,
    records: list[dict[str, Any]],
    **options: Any,
) -> dict[str, Any]:
    """Run the legacy raw-pooling path against a local PCM crop."""

    groups, tokens = _prepare(records, options.get("text_field", "alignment_text"))
    started = time.monotonic()
    alignments = aligner.align_tokens(audio_path=audio_path, tokens=tokens)
    return _project(
        records,
        groups,
        alignments,
        elapsed_s=time.monotonic() - started,
        **options,
    )


def align_remote_window(
    aligner: Qwen3AdapterClient,
    audio_id: str,
    records: list[dict[str, Any]],
    **options: Any,
) -> dict[str, Any]:
    """Run a source-clock crop through the compact colocated adapter."""

    prepared = prepare_alignment_window(
        records, options.get("text_field", "alignment_text")
    )
    return align_prepared_remote_window(
        aligner,
        audio_id,
        prepared,
        **options,
    )


def prepare_alignment_window(
    records: list[dict[str, Any]], text_field: str
) -> PreparedAlignmentWindow:
    """Tokenize one window before its request enters the concurrent worker pool."""

    groups, tokens = _prepare(records, text_field)
    return PreparedAlignmentWindow(
        records=records,
        groups=groups,
        tokens=tokens,
        text_field=text_field,
    )


def align_prepared_remote_window(
    aligner: Qwen3AdapterClient,
    audio_id: str,
    prepared: PreparedAlignmentWindow,
    **options: Any,
) -> dict[str, Any]:
    """Send one already-tokenized window through the compact adapter."""

    started = time.monotonic()
    alignments = aligner.align_crop(
        audio_id=audio_id,
        crop_start_s=float(options["crop_start_s"]),
        crop_end_s=float(options["crop_end_s"]),
        tokens=prepared.tokens,
    )
    project_options = {**options, "text_field": prepared.text_field}
    return _project(
        prepared.records,
        prepared.groups,
        alignments,
        elapsed_s=time.monotonic() - started,
        **project_options,
    )


def _prepare(
    records: list[dict[str, Any]], text_field: str
) -> tuple[list[AlignmentTextGroup], list[AlignmentToken]]:
    groups = [
        AlignmentTextGroup(
            id=str(row["cue_id"]),
            text=str(row[text_field]),
            metadata={
                "source_index": row["source_index"],
                "cleaned_index": row["cleaned_index"],
            },
        )
        for row in records
    ]
    tokens = [token for group in groups for token in segment_group_with_nagisa(group)]
    return groups, tokens


def _project(
    records: list[dict[str, Any]],
    groups: list[AlignmentTextGroup],
    token_alignments: list[TokenAlignment],
    *,
    target_id: str | None,
    target_name: str,
    crop_start_s: float,
    crop_end_s: float,
    elapsed_s: float,
    text_field: str = "alignment_text",
) -> dict[str, Any]:
    merged = merge_token_alignments_by_group(groups, token_alignments)
    tokens_by_group = {
        group.id: [item for item in token_alignments if item.token.group_id == group.id]
        for group in groups
    }
    cues = []
    for row in records:
        local = merged[str(row["cue_id"])]
        cues.append(
            {
                "cue_id": row["cue_id"],
                "source_index": row["source_index"],
                "cleaned_index": row["cleaned_index"],
                "text": row[text_field],
                "source_start_s": row["source_start_s"],
                "source_end_s": row["source_end_s"],
                "aligned_start_s": _global_time(local.start_s, crop_start_s),
                "aligned_end_s": _global_time(local.end_s, crop_start_s),
                "status": local.status,
                "token_count": local.metadata.get("token_count", 0),
                "score_signals": summarize_token_scores(
                    tokens_by_group[str(row["cue_id"])]
                ),
            }
        )
    target = (
        next(item for item in cues if item["cue_id"] == target_id)
        if target_id is not None
        else None
    )
    return {
        "target": target_name,
        "target_cue_id": target_id,
        "crop_start_s": crop_start_s,
        "crop_end_s": crop_end_s,
        "window_duration_s": crop_end_s - crop_start_s,
        "cue_count": len(groups),
        "token_count": len(token_alignments),
        "request_elapsed_s": elapsed_s,
        "target_result": target,
        "cues": cues,
    }


def _global_time(value: float | None, crop_start_s: float) -> float | None:
    return None if value is None else value + crop_start_s
