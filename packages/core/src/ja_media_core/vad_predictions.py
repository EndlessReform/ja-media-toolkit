"""Threshold-independent VAD prediction contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from ja_media_core.audio import AudioChunk
from ja_media_core.vad import (
    SpeechSpan,
    VadBackend,
    VadOptions,
    VadTimeline,
    normalize_speech_spans,
)


@dataclass(frozen=True)
class VadPrediction:
    start_s: float
    end_s: float
    speech_probability: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VadPredictionTimeline:
    """Threshold-independent model scores on the source clock."""

    chunk: AudioChunk
    predictions: list[VadPrediction]
    backend: str
    metadata: dict[str, Any] = field(default_factory=dict)


class PredictedVadBackend(VadBackend, Protocol):
    def predict(
        self,
        chunks: Sequence[AudioChunk],
        *,
        options: VadOptions | None = None,
    ) -> list[VadPredictionTimeline]:
        ...


def speech_timeline_from_predictions(
    prediction_timeline: VadPredictionTimeline,
    *,
    options: VadOptions | None = None,
) -> VadTimeline:
    """Build a normal speech timeline from cached prediction windows."""

    active_options = VadOptions() if options is None else options
    threshold = 0.5 if active_options.threshold is None else active_options.threshold
    spans = [
        SpeechSpan(
            start_s=prediction.start_s,
            end_s=prediction.end_s,
            metadata={
                **prediction.metadata,
                "speech_probability": prediction.speech_probability,
            },
        )
        for prediction in prediction_timeline.predictions
        if prediction.speech_probability >= threshold
    ]
    speech = normalize_speech_spans(
        spans,
        start_s=prediction_timeline.chunk.start_s,
        end_s=prediction_timeline.chunk.end_s,
        min_duration_s=active_options.min_speech_s,
        merge_gap_s=active_options.merge_gap_s,
        pad_s=active_options.speech_pad_s,
    )
    return VadTimeline(
        chunk=prediction_timeline.chunk,
        speech=speech,
        backend=prediction_timeline.backend,
        metadata={
            **prediction_timeline.metadata,
            **active_options.metadata,
            "threshold": threshold,
            "model_api": "cached_predictions",
        },
    )
