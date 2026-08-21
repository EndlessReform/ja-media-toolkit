"""Environment-owned execution for one subtitle-LID envelope."""

from __future__ import annotations

from datetime import UTC, datetime
import time

from ja_media_core.subtitle_lid import analyze_subtitle_language
from ja_media_core.transcripts import parse_ass, parse_srt

from ja_media_data.products.identities import fingerprint
from ja_media_data.storage.bronze import BronzeStore
from ja_media_data.workers.contracts import (
    ResultEnvelope,
    SubtitleLidRequest,
    SubtitleLidResult,
    WorkEnvelope,
)
from ja_media_data.workers.marker_store import MarkerStore


def execute_lid_item(
    envelope: WorkEnvelope,
    *,
    bronze: BronzeStore,
    markers: MarkerStore,
) -> ResultEnvelope:
    """Classify one subtitle and commit its marker last."""

    request = envelope.payload
    if not isinstance(request, SubtitleLidRequest):
        raise ValueError(f"unsupported worker operation: {request.operation}")
    if request.source.bucket != bronze.bucket:
        raise ValueError("subtitle source bucket is not the configured bronze bucket")
    if request.staging_bucket != markers.bucket:
        raise ValueError("request staging bucket does not match worker profile")
    if request.staging_prefix.strip("/") != markers.prefix:
        raise ValueError("request staging prefix does not match worker profile")
    marker_key = markers.key_for(envelope.request_id)
    existing = markers.read_text(marker_key)
    if existing is not None:
        return _validated_existing(existing, envelope, request)

    started = time.monotonic()
    text = bronze.read_text(request.source.key, expected_etag=request.source.etag)
    codec = (request.codec or "").casefold()
    if request.source.key.casefold().endswith(".srt"):
        cues = parse_srt(text, source_path=request.source.key)
    elif codec in {"ass", "ssa"}:
        cues = parse_ass(text, source_path=request.source.key)
    else:
        cues = parse_srt(text, source_path=request.source.key)
    analysis = analyze_subtitle_language(cues, config=request.recipe_parameters)
    output_fingerprint = fingerprint(
        request.subtitle_input_id,
        request.input_fingerprint,
        request.recipe_revision,
        analysis.language.value,
        analysis.reason,
        analysis.script,
        analysis.sampled,
    )
    result = ResultEnvelope(
        request_id=envelope.request_id,
        completed_at=datetime.now(UTC),
        result=SubtitleLidResult(
            subtitle_input_id=request.subtitle_input_id,
            language=analysis.language,
            reason=analysis.reason,
            script_metrics=analysis.script,
            sampled_metrics=analysis.sampled,
            input_fingerprint=request.input_fingerprint,
            recipe_revision=request.recipe_revision,
            output_fingerprint=output_fingerprint,
            elapsed_seconds=time.monotonic() - started,
        ),
    )
    markers.write_text(marker_key, result.model_dump_json())
    return result


def _validated_existing(
    body: str,
    envelope: WorkEnvelope,
    request: SubtitleLidRequest,
) -> ResultEnvelope:
    result = ResultEnvelope.model_validate_json(body)
    item = result.result
    if not isinstance(item, SubtitleLidResult):
        raise RuntimeError("existing marker contains another operation")
    if result.request_id != envelope.request_id:
        raise RuntimeError("existing marker request ID does not match")
    if (
        item.subtitle_input_id != request.subtitle_input_id
        or item.input_fingerprint != request.input_fingerprint
        or item.recipe_revision != request.recipe_revision
    ):
        raise RuntimeError("existing marker does not match requested LID product")
    return result
