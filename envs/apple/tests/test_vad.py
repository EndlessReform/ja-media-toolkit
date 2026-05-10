from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np

from ja_media_apple.cli import _dump_speech_chunks
from ja_media_apple.vad import MlxAudioVadBackend
from ja_media_core.audio import AudioChunk, probe_audio_source, resolve_audio_source
from ja_media_core.vad import VadOptions


REPO_ROOT = Path(__file__).resolve().parents[3]
JFK_WAV = REPO_ROOT / "examples" / "input" / "jfk.wav"


@dataclass
class FakeSegment:
    start: float
    end: float
    speaker: int = 0


@dataclass
class FakeResult:
    segments: list[FakeSegment]
    num_speakers: int = 1


class FakeMlxAudioModel:
    def __init__(self, result: FakeResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def generate(self, audio: np.ndarray, **kwargs: Any) -> FakeResult:
        self.calls.append({"audio": audio, **kwargs})
        return self.result


class FakeSileroModel:
    def __init__(self, timestamps: list[dict[str, float]]) -> None:
        self.timestamps = timestamps
        self.calls: list[dict[str, Any]] = []

    def get_speech_timestamps(self, audio: np.ndarray, **kwargs: Any) -> list[dict[str, float]]:
        self.calls.append({"audio": audio, **kwargs})
        return self.timestamps


class MlxAudioVadBackendTest(unittest.TestCase):
    def test_detect_uses_silero_timestamps_when_available(self) -> None:
        fake_model = FakeSileroModel(
            [
                {"start": 0.25, "end": 0.75},
                {"start": 1.25, "end": 1.50},
            ]
        )
        backend = MlxAudioVadBackend(
            model_id="fake-silero",
            model_loader=lambda *args, **kwargs: fake_model,
        )

        timeline = backend.detect(
            [_jfk_chunk(2.0, 4.0)],
            options=VadOptions(
                threshold=0.35,
                min_speech_s=0.20,
                min_silence_s=0.10,
                speech_pad_s=0.0,
            ),
        )[0]

        self.assertEqual(len(timeline.speech), 2)
        self.assertAlmostEqual(timeline.speech[0].start_s, 2.25)
        self.assertAlmostEqual(timeline.speech[0].end_s, 2.75)
        self.assertEqual(timeline.metadata["model_api"], "get_speech_timestamps")
        self.assertEqual(fake_model.calls[0]["threshold"], 0.35)
        self.assertEqual(fake_model.calls[0]["min_silence_duration_ms"], 100)

    def test_detect_uses_mlx_audio_segments_as_source_timeline_speech(self) -> None:
        fake_model = FakeMlxAudioModel(
            FakeResult(
                segments=[
                    FakeSegment(0.20, 0.50, speaker=0),
                    FakeSegment(0.45, 0.90, speaker=1),
                ],
                num_speakers=2,
            )
        )
        backend = MlxAudioVadBackend(
            model_id="fake-model",
            model_loader=lambda *args, **kwargs: fake_model,
        )

        timeline = backend.detect(
            [_jfk_chunk(1.0, 3.0)],
            options=VadOptions(threshold=0.4, min_speech_s=0.10, speech_pad_s=0.0),
        )[0]

        self.assertEqual(timeline.backend, "mlx-audio")
        self.assertEqual(len(timeline.speech), 1)
        self.assertAlmostEqual(timeline.speech[0].start_s, 1.20)
        self.assertAlmostEqual(timeline.speech[0].end_s, 1.90)
        self.assertEqual(timeline.metadata["model_id"], "fake-model")
        self.assertEqual(timeline.metadata["raw_segment_count"], 2)
        self.assertEqual(timeline.metadata["num_speakers"], 2)
        self.assertEqual(fake_model.calls[0]["sample_rate"], 16_000)
        self.assertEqual(fake_model.calls[0]["threshold"], 0.4)
        self.assertEqual(fake_model.calls[0]["audio"].ndim, 1)

    def test_detect_speech_chunks_returns_audio_chunks_for_asr_chain(self) -> None:
        fake_model = FakeMlxAudioModel(
            FakeResult(
                segments=[
                    FakeSegment(0.10, 0.15),
                    FakeSegment(0.40, 1.10),
                ]
            )
        )
        backend = MlxAudioVadBackend(
            model_id="fake-model",
            model_loader=lambda *args, **kwargs: fake_model,
        )

        chunks = backend.detect_speech_chunks(
            [_jfk_chunk(2.0, 4.0)],
            min_duration_s=0.25,
            options=VadOptions(speech_pad_s=0.0),
            kind="asr_chunk",
            metadata={"purpose": "smoke"},
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].kind, "asr_chunk")
        self.assertAlmostEqual(chunks[0].start_s, 2.40)
        self.assertAlmostEqual(chunks[0].end_s, 3.10)
        self.assertEqual(chunks[0].metadata["boundary_source"], "vad")
        self.assertEqual(chunks[0].metadata["vad_backend"], "mlx-audio")
        self.assertEqual(chunks[0].metadata["purpose"], "smoke")

    def test_dump_speech_chunks_writes_flacs_for_listening(self) -> None:
        chunks = [_jfk_chunk(0.0, 0.50), _jfk_chunk(0.50, 1.0)]

        with TemporaryDirectory() as tmpdir:
            paths = _dump_speech_chunks(
                chunks,
                output_dir=Path(tmpdir),
                source_id="jfk",
            )

            self.assertEqual(len(paths), 2)
            self.assertTrue(paths[0].exists())
            self.assertEqual(paths[0].suffix, ".flac")


def _jfk_chunk(start_s: float, end_s: float) -> AudioChunk:
    source = resolve_audio_source(JFK_WAV, must_exist=True)
    audio_format = probe_audio_source(source)
    return AudioChunk(
        source=source,
        start_s=start_s,
        end_s=end_s,
        source_start_frame=round(start_s * audio_format.sample_rate_hz),
        source_end_frame=round(end_s * audio_format.sample_rate_hz),
        format=audio_format,
        kind="test",
    )


if __name__ == "__main__":
    unittest.main()
