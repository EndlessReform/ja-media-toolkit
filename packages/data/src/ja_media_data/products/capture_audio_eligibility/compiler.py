"""Compile canonical-audio eligibility independently from episode binding."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from ja_media_core.bronze import BronzeManifestError, BronzeStream, parse_bronze_manifest

from ja_media_data.products.canonical_inputs.selection import audio_object_key
from ja_media_data.products.capture_audio_eligibility.models import (
    CaptureAudioEligibility,
)
from ja_media_data.products.identities import fingerprint
from ja_media_data.storage.bronze import BronzeDocument, BronzeStore


AUDIO_ELIGIBILITY_POLICY_VERSION = "declared-japanese-audio-v1"
ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
LEGACY_SINGLE_AUDIO_TRACK = "legacy_manifest_has_single_audio_track"
FIRST_DECLARED_JAPANESE_TRACK = "selected_first_audio_track_declared_japanese"
NO_DECLARED_JAPANESE_TRACK = "no_audio_track_declared_japanese"
MANIFEST_FAILED_SCHEMA_VALIDATION = "bronze_manifest_failed_schema_validation"


@dataclass(frozen=True)
class CompiledCaptureAudioEligibility:
    """The complete capture decision set and its deterministic corpus identity."""

    rows: tuple[CaptureAudioEligibility, ...]
    fingerprint: str


def compile_product(
    documents: list[BronzeDocument], store: BronzeStore
) -> CompiledCaptureAudioEligibility:
    """Evaluate every committed manifest without turning rejection into failure."""

    rows = tuple(
        _evaluate(document, store)
        for document in sorted(
            documents, key=lambda item: (item.marker.key, item.marker.capture_id)
        )
    )
    return CompiledCaptureAudioEligibility(
        rows=rows,
        fingerprint=fingerprint(
            AUDIO_ELIGIBILITY_POLICY_VERSION, [asdict(row) for row in rows]
        ),
    )


def _evaluate(
    document: BronzeDocument, store: BronzeStore
) -> CaptureAudioEligibility:
    marker = document.marker
    try:
        manifest = parse_bronze_manifest(
            document.manifest,
            capture_id=marker.capture_id,
            manifest_key=marker.key,
        )
    except BronzeManifestError:
        return _row(
            document,
            store,
            schema_version=None,
            status=INELIGIBLE,
            reason=MANIFEST_FAILED_SCHEMA_VALIDATION,
            selected=None,
            tracks=(),
        )

    if manifest.schema_version == 1:
        selected = manifest.audio
        reason = LEGACY_SINGLE_AUDIO_TRACK
    else:
        selected = next(
            (
                track
                for track in manifest.audio_tracks
                if track.declared_language == "jpn"
            ),
            None,
        )
        reason = (
            FIRST_DECLARED_JAPANESE_TRACK
            if selected is not None
            else NO_DECLARED_JAPANESE_TRACK
        )
    return _row(
        document,
        store,
        schema_version=manifest.schema_version,
        status=ELIGIBLE if selected is not None else INELIGIBLE,
        reason=reason,
        selected=selected,
        tracks=manifest.audio_tracks,
    )


def _row(
    document: BronzeDocument,
    store: BronzeStore,
    *,
    schema_version: int | None,
    status: str,
    reason: str,
    selected: BronzeStream | None,
    tracks: tuple[BronzeStream, ...],
) -> CaptureAudioEligibility:
    marker = document.marker
    track_context = json.dumps(
        [asdict(track) for track in tracks],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    selected_key = (
        audio_object_key(marker.key, selected.object_name) if selected else None
    )
    row_fingerprint = fingerprint(
        AUDIO_ELIGIBILITY_POLICY_VERSION,
        marker.capture_id,
        marker.key,
        marker.etag,
        schema_version,
        status,
        reason,
        selected_key,
        selected.stream_index if selected else None,
        track_context,
    )
    return CaptureAudioEligibility(
        capture_id=marker.capture_id,
        manifest_bucket=store.bucket,
        manifest_key=marker.key,
        manifest_etag=marker.etag,
        manifest_schema_version=schema_version,
        status=status,
        reason=reason,
        selected_audio_object_bucket=store.bucket if selected else None,
        selected_audio_object_key=selected_key,
        selected_audio_stream_index=selected.stream_index if selected else None,
        selected_audio_codec=selected.codec if selected else None,
        selected_audio_declared_language=(
            selected.declared_language if selected else None
        ),
        available_audio_tracks=track_context,
        input_fingerprint=row_fingerprint,
    )
