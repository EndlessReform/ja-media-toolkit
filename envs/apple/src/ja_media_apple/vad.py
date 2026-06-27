from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Sequence

import numpy as np
from numpy.typing import NDArray

from ja_media_core.audio import AudioChunk, materialize_audio_chunk
from ja_media_core.vad_predictions import VadPredictionTimeline
from ja_media_core.vad import (
    SpeechSpan,
    VadOptions,
    VadTimeline,
    normalize_speech_spans,
    speech_chunks_from_timelines,
)
from ja_media_apple.vad_predictions import (
    SILERO_SAMPLE_RATE_HZ,
    predict_chunk_with_model,
)


DEFAULT_MLX_AUDIO_VAD_MODEL = "mlx-community/silero-vad"


ModelLoader = Callable[..., Any]


class MlxAudioVadBackend:
    name = "mlx-audio"

    def __init__(
        self,
        model_id: str = DEFAULT_MLX_AUDIO_VAD_MODEL,
        *,
        model_loader: ModelLoader | None = None,
        lazy: bool = False,
        strict: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.model_id = model_id
        self.lazy = lazy
        self.strict = strict
        self.metadata = {} if metadata is None else dict(metadata)
        self._model_loader = model_loader
        self._model: Any | None = None

    def detect(
        self,
        chunks: Sequence[AudioChunk],
        *,
        options: VadOptions | None = None,
    ) -> list[VadTimeline]:
        active_options = VadOptions() if options is None else options
        return [self.detect_chunk(chunk, options=active_options) for chunk in chunks]

    def detect_speech_chunks(
        self,
        chunks: Sequence[AudioChunk],
        *,
        min_duration_s: float = 0.25,
        options: VadOptions | None = None,
        kind: str = "speech",
        metadata: dict[str, Any] | None = None,
    ) -> list[AudioChunk]:
        active_options = VadOptions() if options is None else options
        active_options = replace(active_options, min_speech_s=min_duration_s)
        timelines = self.detect(chunks, options=active_options)
        return speech_chunks_from_timelines(
            timelines,
            min_duration_s=min_duration_s,
            kind=kind,
            metadata=metadata,
        )

    def detect_chunk(
        self,
        chunk: AudioChunk,
        *,
        options: VadOptions | None = None,
    ) -> VadTimeline:
        active_options = VadOptions() if options is None else options
        materialized = materialize_audio_chunk(
            chunk,
            target_sample_rate_hz=SILERO_SAMPLE_RATE_HZ,
        )
        source_chunk = materialized.chunk
        waveform = _mono_waveform(
            materialized.samples,
            channel=active_options.channel,
        )
        model = self._load_model()
        threshold = 0.5 if active_options.threshold is None else active_options.threshold
        spans, result_metadata = _detect_speech_spans(
            model,
            waveform,
            sample_rate_hz=materialized.sample_rate_hz,
            chunk_start_s=source_chunk.start_s,
            options=active_options,
            threshold=threshold,
        )
        speech = normalize_speech_spans(
            spans,
            start_s=source_chunk.start_s,
            end_s=source_chunk.end_s,
            min_duration_s=active_options.min_speech_s,
            merge_gap_s=active_options.merge_gap_s,
            pad_s=active_options.speech_pad_s,
        )

        return VadTimeline(
            chunk=source_chunk,
            speech=speech,
            backend=self.name,
            metadata={
                **self.metadata,
                **active_options.metadata,
                "model_id": self.model_id,
                "source_sample_rate_hz": materialized.source_sample_rate_hz,
                "vad_sample_rate_hz": materialized.sample_rate_hz,
                "threshold": threshold,
                "min_speech_s": active_options.min_speech_s,
                "min_silence_s": active_options.min_silence_s,
                "merge_gap_s": active_options.merge_gap_s,
                "speech_pad_s": active_options.speech_pad_s,
                **result_metadata,
            },
        )

    def predict(
        self,
        chunks: Sequence[AudioChunk],
        *,
        options: VadOptions | None = None,
    ) -> list[VadPredictionTimeline]:
        active_options = VadOptions() if options is None else options
        return [self.predict_chunk(chunk, options=active_options) for chunk in chunks]

    def predict_chunk(
        self,
        chunk: AudioChunk,
        *,
        options: VadOptions | None = None,
    ) -> VadPredictionTimeline:
        active_options = VadOptions() if options is None else options
        return predict_chunk_with_model(
            chunk,
            options=active_options,
            model=self._load_model(),
            backend=self.name,
            model_id=self.model_id,
            metadata=self.metadata,
        )

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        loader = self._model_loader
        if loader is None:
            from mlx_audio.vad import load

            loader = load

        self._model = loader(
            self.model_id,
            lazy=self.lazy,
            strict=self.strict,
        )
        if not (
            hasattr(self._model, "generate")
            or hasattr(self._model, "get_speech_timestamps")
        ):
            raise TypeError(
                "mlx-audio VAD model must expose generate() or get_speech_timestamps()"
            )
        return self._model


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


def _detect_speech_spans(
    model: Any,
    waveform: NDArray[np.float32],
    *,
    sample_rate_hz: int,
    chunk_start_s: float,
    options: VadOptions,
    threshold: float,
) -> tuple[list[SpeechSpan], dict[str, Any]]:
    if hasattr(model, "get_speech_timestamps"):
        timestamps = model.get_speech_timestamps(
            waveform,
            sample_rate=sample_rate_hz,
            threshold=threshold,
            min_speech_duration_ms=round(options.min_speech_s * 1000),
            min_silence_duration_ms=round(options.min_silence_s * 1000),
            speech_pad_ms=round(options.speech_pad_s * 1000),
            return_seconds=True,
        )
        return (
            _speech_spans_from_silero_timestamps(
                timestamps,
                chunk_start_s=chunk_start_s,
            ),
            {
                "raw_segment_count": len(timestamps),
                "model_api": "get_speech_timestamps",
                "num_speakers": None,
            },
        )

    result = model.generate(
        waveform,
        sample_rate=sample_rate_hz,
        threshold=threshold,
        min_duration=options.min_speech_s,
        merge_gap=options.merge_gap_s,
        verbose=False,
    )
    return (
        _speech_spans_from_mlx_audio_result(
            result,
            chunk_start_s=chunk_start_s,
        ),
        {
            "raw_segment_count": len(getattr(result, "segments", []) or []),
            "model_api": "generate",
            "num_speakers": getattr(result, "num_speakers", None),
        },
    )


def _speech_spans_from_silero_timestamps(
    timestamps: list[dict[str, Any]],
    *,
    chunk_start_s: float,
) -> list[SpeechSpan]:
    spans: list[SpeechSpan] = []
    for timestamp in timestamps:
        spans.append(
            SpeechSpan(
                start_s=chunk_start_s + float(timestamp["start"]),
                end_s=chunk_start_s + float(timestamp["end"]),
                metadata={"model_api": "get_speech_timestamps"},
            )
        )
    return spans


def _speech_spans_from_mlx_audio_result(
    result: Any,
    *,
    chunk_start_s: float,
) -> list[SpeechSpan]:
    segments = getattr(result, "segments", None)
    if segments is None:
        raise TypeError("mlx-audio VAD result must expose a segments attribute")

    spans: list[SpeechSpan] = []
    for segment in segments:
        start_s = chunk_start_s + float(getattr(segment, "start"))
        end_s = chunk_start_s + float(getattr(segment, "end"))
        metadata: dict[str, Any] = {}
        speaker = getattr(segment, "speaker", None)
        if speaker is not None:
            metadata["speaker"] = speaker
        spans.append(SpeechSpan(start_s=start_s, end_s=end_s, metadata=metadata))
    return spans
