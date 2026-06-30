"""Serialization for forced-alignment run contracts."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from ja_media_core.audio import AudioChunk, AudioFormat, AudioSource
from ja_media_core.forced_alignment import (
    AlignmentArtifactRef,
    AlignmentDiagnostic,
    AlignmentRunManifest,
    AlignmentSpan,
    AlignmentWindow,
    AlignmentWindowResult,
    SourceCueRef,
    SpanAlignment,
)


SCHEMA_VERSION = 1
MANIFEST_KIND = "forced-alignment-run"
WINDOW_KIND = "forced-alignment-window"
WINDOW_RESULT_KIND = "forced-alignment-window-result"


def manifest_to_mapping(manifest: AlignmentRunManifest) -> dict[str, object]:
    """Convert a run manifest to its stable JSON mapping."""

    return {
        "schema_version": manifest.schema_version,
        "kind": MANIFEST_KIND,
        "id": manifest.id,
        "purpose": manifest.purpose,
        "created_at": manifest.created_at,
        "inputs": [_artifact_to_mapping(item) for item in manifest.inputs],
        "backend": dict(manifest.backend),
        "span_policy": manifest.span_policy,
        "window_policy": dict(manifest.window_policy),
        "outputs": [_artifact_to_mapping(item) for item in manifest.outputs],
        "diagnostics": [_diagnostic_to_mapping(item) for item in manifest.diagnostics],
        "metadata": dict(manifest.metadata),
    }


def manifest_from_mapping(payload: Mapping[str, Any]) -> AlignmentRunManifest:
    """Validate and decode one forced-alignment run manifest mapping."""

    _require_schema(payload, MANIFEST_KIND)
    return AlignmentRunManifest(
        id=str(payload["id"]),
        purpose=payload["purpose"],
        schema_version=int(payload["schema_version"]),
        created_at=str(payload["created_at"]),
        inputs=tuple(_artifact_from_mapping(item) for item in _list(payload, "inputs")),
        backend=dict(_mapping(payload, "backend")),
        span_policy=str(payload["span_policy"]),
        window_policy=dict(_mapping(payload, "window_policy")),
        outputs=tuple(_artifact_from_mapping(item) for item in _list(payload, "outputs")),
        diagnostics=tuple(
            _diagnostic_from_mapping(item) for item in _list(payload, "diagnostics")
        ),
        metadata=dict(_mapping(payload, "metadata")),
    )


def window_to_mapping(window: AlignmentWindow) -> dict[str, object]:
    """Convert an alignment window to its stable JSON mapping."""

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": WINDOW_KIND,
        "id": window.id,
        "audio": _audio_chunk_to_mapping(window.audio),
        "spans": [_span_to_mapping(item) for item in window.spans],
        "language": window.language,
        "context": window.context,
        "metadata": dict(window.metadata),
    }


def window_from_mapping(payload: Mapping[str, Any]) -> AlignmentWindow:
    """Validate and decode one alignment window mapping."""

    _require_schema(payload, WINDOW_KIND)
    return AlignmentWindow(
        id=str(payload["id"]),
        audio=_audio_chunk_from_mapping(_mapping(payload, "audio")),
        spans=tuple(_span_from_mapping(item) for item in _list(payload, "spans")),
        language=payload.get("language"),
        context=payload.get("context"),
        metadata=dict(_mapping(payload, "metadata")),
    )


def result_to_mapping(result: AlignmentWindowResult) -> dict[str, object]:
    """Convert a backend window result to its stable JSON mapping."""

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": WINDOW_RESULT_KIND,
        "window_id": result.window_id,
        "backend": result.backend,
        "status": result.status,
        "alignments": [_alignment_to_mapping(item) for item in result.alignments],
        "diagnostics": [_diagnostic_to_mapping(item) for item in result.diagnostics],
        "metadata": dict(result.metadata),
    }


def result_from_mapping(payload: Mapping[str, Any]) -> AlignmentWindowResult:
    """Validate and decode one backend window result mapping."""

    _require_schema(payload, WINDOW_RESULT_KIND)
    return AlignmentWindowResult(
        window_id=str(payload["window_id"]),
        backend=str(payload["backend"]),
        status=payload["status"],
        alignments=tuple(
            _alignment_from_mapping(item) for item in _list(payload, "alignments")
        ),
        diagnostics=tuple(
            _diagnostic_from_mapping(item) for item in _list(payload, "diagnostics")
        ),
        metadata=dict(_mapping(payload, "metadata")),
    )


def _require_schema(payload: Mapping[str, Any], kind: str) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("kind") != kind:
        raise ValueError(f"unsupported {kind} schema")


def _artifact_to_mapping(ref: AlignmentArtifactRef) -> dict[str, object]:
    return asdict(ref)


def _artifact_from_mapping(payload: object) -> AlignmentArtifactRef:
    return AlignmentArtifactRef(**dict(_object_mapping(payload, "artifact ref")))


def _span_to_mapping(span: AlignmentSpan) -> dict[str, object]:
    return {
        **asdict(span),
        "source_cue": (
            _cue_ref_to_mapping(span.source_cue) if span.source_cue is not None else None
        ),
    }


def _span_from_mapping(payload: object) -> AlignmentSpan:
    data = dict(_object_mapping(payload, "alignment span"))
    cue = data.get("source_cue")
    data["source_cue"] = _cue_ref_from_mapping(cue) if cue is not None else None
    return AlignmentSpan(**data)


def _alignment_to_mapping(alignment: SpanAlignment) -> dict[str, object]:
    return {
        **asdict(alignment),
        "diagnostics": [
            _diagnostic_to_mapping(item) for item in alignment.diagnostics
        ],
    }


def _alignment_from_mapping(payload: object) -> SpanAlignment:
    data = dict(_object_mapping(payload, "span alignment"))
    data["diagnostics"] = tuple(
        _diagnostic_from_mapping(item) for item in data.get("diagnostics", ())
    )
    return SpanAlignment(**data)


def _diagnostic_to_mapping(diagnostic: AlignmentDiagnostic) -> dict[str, object]:
    return {
        **asdict(diagnostic),
        "source_cue": (
            _cue_ref_to_mapping(diagnostic.source_cue)
            if diagnostic.source_cue is not None
            else None
        ),
    }


def _diagnostic_from_mapping(payload: object) -> AlignmentDiagnostic:
    data = dict(_object_mapping(payload, "alignment diagnostic"))
    cue = data.get("source_cue")
    data["source_cue"] = _cue_ref_from_mapping(cue) if cue is not None else None
    return AlignmentDiagnostic(**data)


def _cue_ref_to_mapping(ref: SourceCueRef) -> dict[str, object]:
    return asdict(ref)


def _cue_ref_from_mapping(payload: object) -> SourceCueRef:
    return SourceCueRef(**dict(_object_mapping(payload, "source cue ref")))


def _audio_chunk_to_mapping(chunk: AudioChunk) -> dict[str, object]:
    return asdict(chunk)


def _audio_chunk_from_mapping(payload: Mapping[str, Any]) -> AudioChunk:
    data = dict(payload)
    data["source"] = AudioSource(**dict(_mapping(data, "source")))
    raw_format = data.get("format")
    data["format"] = AudioFormat(**dict(raw_format)) if isinstance(raw_format, Mapping) else None
    return AudioChunk(**data)


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _list(payload: Mapping[str, Any], key: str) -> list[object]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _object_mapping(payload: object, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be an object")
    return payload
