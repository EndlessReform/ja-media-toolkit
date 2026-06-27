"""Raw prediction helpers for mlx-audio VAD wrappers."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from ja_media_core.audio import AudioChunk, materialize_audio_chunk
from ja_media_core.vad import VadOptions
from ja_media_core.vad_predictions import VadPrediction, VadPredictionTimeline

SILERO_SAMPLE_RATE_HZ = 16_000


def predict_chunk_with_model(
    chunk: AudioChunk,
    *,
    options: VadOptions,
    model: Any,
    backend: str,
    model_id: str,
    metadata: dict[str, Any],
) -> VadPredictionTimeline:
    materialized = materialize_audio_chunk(
        chunk,
        target_sample_rate_hz=SILERO_SAMPLE_RATE_HZ,
    )
    source_chunk = materialized.chunk
    waveform = _mono_waveform(materialized.samples, channel=options.channel)
    predictions, result_metadata = _predict_speech_probabilities(
        model,
        waveform,
        sample_rate_hz=materialized.sample_rate_hz,
        chunk_start_s=source_chunk.start_s,
    )
    return VadPredictionTimeline(
        chunk=source_chunk,
        predictions=predictions,
        backend=backend,
        metadata={
            **metadata,
            **options.metadata,
            "model_id": model_id,
            "source_sample_rate_hz": materialized.source_sample_rate_hz,
            "vad_sample_rate_hz": materialized.sample_rate_hz,
            **result_metadata,
        },
    )


def _mono_waveform(
    samples: NDArray[np.float32],
    *,
    channel: int | None = None,
) -> NDArray[np.float32]:
    if samples.ndim == 1:
        return samples.astype(np.float32, copy=False)
    if samples.ndim != 2:
        raise ValueError(f"Expected 1-D or 2-D audio samples, got shape {samples.shape}")
    if channel is not None:
        if channel < 0 or channel >= samples.shape[1]:
            raise ValueError(
                f"Requested VAD channel {channel}, but audio has {samples.shape[1]} channels"
            )
        return samples[:, channel].astype(np.float32, copy=False)
    return samples.mean(axis=1, dtype=np.float32)


def _predict_speech_probabilities(
    model: Any,
    waveform: NDArray[np.float32],
    *,
    sample_rate_hz: int,
    chunk_start_s: float,
) -> tuple[list[VadPrediction], dict[str, Any]]:
    for name in ("predict", "predict_proba", "speech_probabilities"):
        predictor = getattr(model, name, None)
        if predictor is None:
            continue
        raw_predictions = predictor(waveform, sample_rate=sample_rate_hz)
        return (
            _prediction_windows_from_result(
                raw_predictions,
                sample_rate_hz=sample_rate_hz,
                chunk_start_s=chunk_start_s,
            ),
            {"model_api": name},
        )
    raise TypeError("mlx-audio VAD model does not expose raw speech probabilities")


def _prediction_windows_from_result(
    result: Any,
    *,
    sample_rate_hz: int,
    chunk_start_s: float,
) -> list[VadPrediction]:
    if isinstance(result, tuple) and len(result) == 2:
        probabilities, window_s = result
        return _prediction_windows_from_probabilities(
            probabilities,
            window_s=float(window_s),
            chunk_start_s=chunk_start_s,
        )

    predictions = getattr(result, "predictions", result)
    if isinstance(predictions, np.ndarray):
        return _prediction_windows_from_probabilities(
            predictions,
            window_s=512 / sample_rate_hz,
            chunk_start_s=chunk_start_s,
        )

    windows: list[VadPrediction] = []
    for item in predictions:
        if isinstance(item, dict):
            start = float(item["start"])
            end = float(item["end"])
            probability_value = item.get("speech_probability", item.get("probability"))
        else:
            start = float(getattr(item, "start"))
            end = float(getattr(item, "end"))
            probability_value = getattr(item, "speech_probability", None)
            if probability_value is None:
                probability_value = getattr(item, "probability")
        windows.append(
            VadPrediction(
                start_s=chunk_start_s + start,
                end_s=chunk_start_s + end,
                speech_probability=float(probability_value),
            )
        )
    return windows


def _prediction_windows_from_probabilities(
    probabilities: Any,
    *,
    window_s: float,
    chunk_start_s: float,
) -> list[VadPrediction]:
    values = np.asarray(probabilities, dtype=np.float32).reshape(-1)
    return [
        VadPrediction(
            start_s=chunk_start_s + index * window_s,
            end_s=chunk_start_s + (index + 1) * window_s,
            speech_probability=float(value),
        )
        for index, value in enumerate(values)
    ]
