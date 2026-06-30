from __future__ import annotations

import json

import pytest

from ja_media_core.audio import AudioChunk, AudioFormat, AudioSource
from ja_media_core.config import JaMediaConfig
from ja_media_core.forced_alignment import (
    AlignmentArtifactRef,
    AlignmentDiagnostic,
    AlignmentRunManifest,
    AlignmentSpan,
    AlignmentWindow,
    AlignmentWindowResult,
    SourceCueRef,
    SpanAlignment,
    source_cue_ref_from_cue,
)
from ja_media_core.forced_alignment_serialization import (
    manifest_from_mapping,
    manifest_to_mapping,
    result_from_mapping,
    result_to_mapping,
    window_from_mapping,
    window_to_mapping,
)
from ja_media_core.transcripts import SubtitleCue


def test_window_json_round_trip_preserves_cue_correlation() -> None:
    cue = SubtitleCue(
        source_path="/subs/episode01.srt",
        index=7,
        start_s=12.0,
        end_s=14.0,
        text="これはテストです",
        metadata={"block_index": 3},
    )
    cue_ref = source_cue_ref_from_cue(cue, source_id="candidate-a")
    window = AlignmentWindow(
        id="episode01-window-0001",
        audio=_audio_chunk(),
        spans=(
            AlignmentSpan(
                id="candidate-a:7:0",
                text="これ",
                source_cue=cue_ref,
                cue_char_start=0,
                cue_char_end=2,
            ),
        ),
        context="candidate-a cue 7",
    )

    restored = window_from_mapping(json.loads(json.dumps(window_to_mapping(window))))

    assert restored == window
    assert restored.spans[0].source_cue == SourceCueRef(
        source_id="candidate-a",
        cue_index=7,
        cue_block_index=3,
        source_path="/subs/episode01.srt",
    )


def test_result_json_round_trip_preserves_diagnostics() -> None:
    cue_ref = SourceCueRef(
        source_id="candidate-a",
        cue_index=7,
        cue_block_index=3,
        source_path="/subs/episode01.srt",
    )
    diagnostic = AlignmentDiagnostic(
        code="non_monotonic_timestamp",
        message="Predicted timestamp moved backward within the cue.",
        severity="warning",
        window_id="episode01-window-0001",
        span_id="candidate-a:7:1",
        source_cue=cue_ref,
        metadata={"previous_s": 13.2, "predicted_s": 12.9},
    )
    result = AlignmentWindowResult(
        window_id="episode01-window-0001",
        backend="qwen3-vllm",
        status="partial",
        alignments=(
            SpanAlignment(
                span_id="candidate-a:7:0",
                start_s=12.4,
                end_s=12.8,
                confidence=0.91,
            ),
            SpanAlignment(
                span_id="candidate-a:7:1",
                start_s=None,
                end_s=None,
                status="suspicious",
                diagnostics=(diagnostic,),
            ),
        ),
        diagnostics=(diagnostic,),
        metadata={"model": "Qwen/Qwen3-ASR"},
    )

    restored = result_from_mapping(json.loads(json.dumps(result_to_mapping(result))))

    assert restored == result
    assert restored.alignments[1].diagnostics[0].source_cue == cue_ref


def test_manifest_json_round_trip_records_artifacts_and_backend() -> None:
    manifest = AlignmentRunManifest(
        id="run-2026-06-29-example",
        purpose="timing_eval",
        schema_version=1,
        created_at="2026-06-29T12:00:00Z",
        inputs=(
            AlignmentArtifactRef(
                uri="file:///audio/episode01.flac",
                media_type="audio/flac",
                sha256="a" * 64,
                size_bytes=123,
            ),
        ),
        backend={"name": "qwen3-vllm", "model": "Qwen/Qwen3-ASR"},
        span_policy="ja-media/simple-cue-lexemes-v1",
        window_policy={"target_duration_s": 30.0},
        outputs=(
            AlignmentArtifactRef(
                uri="file:///runs/example/window-results.jsonl",
                media_type="application/jsonl",
            ),
        ),
    )

    restored = manifest_from_mapping(
        json.loads(json.dumps(manifest_to_mapping(manifest)))
    )

    assert restored == manifest


def test_serialization_rejects_unknown_schema() -> None:
    payload = window_to_mapping(
        AlignmentWindow(
            id="window",
            audio=_audio_chunk(),
            spans=(AlignmentSpan(id="span", text="テスト"),),
        )
    )
    payload["schema_version"] = 2

    with pytest.raises(ValueError, match="unsupported"):
        window_from_mapping(payload)


def test_config_accepts_forced_alignment_backend_entries() -> None:
    config = JaMediaConfig.model_validate(
        {
            "forced_alignment": {
                "default_backend": "local-qwen3",
                "backends": {
                    "local-qwen3": {
                        "type": "qwen3-mlx",
                        "model": "Qwen/Qwen3-ASR",
                    },
                },
            },
        }
    )

    backend_config = config.forced_alignment.get_backend_config()

    assert backend_config.type == "qwen3-mlx"
    assert backend_config.model_extra == {"model": "Qwen/Qwen3-ASR"}


def test_aligned_span_requires_times() -> None:
    with pytest.raises(ValueError, match="start and end"):
        SpanAlignment(span_id="span", start_s=None, end_s=None)


def _audio_chunk() -> AudioChunk:
    audio_format = AudioFormat(
        sample_rate_hz=48_000,
        channels=2,
        duration_s=24.0,
        codec="flac",
        container="flac",
        frame_count=1_152_000,
    )
    return AudioChunk(
        source=AudioSource(
            id="episode01",
            locator="/audio/episode01.flac",
            metadata={"series": "example"},
        ),
        start_s=10.0,
        end_s=20.0,
        source_start_frame=480_000,
        source_end_frame=960_000,
        format=audio_format,
        kind="alignment_window",
    )
