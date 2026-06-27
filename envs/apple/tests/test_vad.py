from __future__ import annotations

import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import numpy as np

from ja_media_apple.cli import _dump_audio_chunks, run_vad_local
from ja_media_apple.vad import MlxAudioVadBackend
from ja_media_core.audio import AudioChunk, probe_audio_source, resolve_audio_source
from ja_media_core.vad import VadOptions, VadTimeline


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


class FakeBackendForCli:
    name = "fake-vad"
    instances: list["FakeBackendForCli"] = []

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.detect_calls: list[dict[str, Any]] = []
        FakeBackendForCli.instances.append(self)

    def detect(self, chunks: list[AudioChunk], *, options: VadOptions) -> Any:
        self.detect_calls.append({"chunks": chunks, "options": options})
        return [VadTimeline(chunk=chunks[0], speech=[], backend=self.name)]


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
        self.assertEqual(timeline.metadata["vad_sample_rate_hz"], 16_000)
        self.assertEqual(fake_model.calls[0]["threshold"], 0.35)
        self.assertEqual(fake_model.calls[0]["sample_rate"], 16_000)
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

    def test_dump_audio_chunks_writes_labeled_files_for_listening(self) -> None:
        chunks = [_jfk_chunk(0.0, 0.50), _jfk_chunk(0.50, 1.0)]

        with TemporaryDirectory() as tmpdir:
            paths = _dump_audio_chunks(
                chunks,
                output_dir=Path(tmpdir),
                source_id="jfk",
                label="split",
                audio_format="wav",
            )
            exported_format = probe_audio_source(
                resolve_audio_source(paths[0], must_exist=True)
            )

            self.assertEqual(len(paths), 2)
            self.assertTrue(paths[0].exists())
            self.assertEqual(paths[0].suffix, ".wav")
            self.assertIn("_split_001_", paths[0].name)
            self.assertIn("_src_000000000ms-000000500ms_", paths[0].name)
            self.assertIn("_dur_000000500ms.", paths[0].name)
            self.assertAlmostEqual(exported_format.duration_s or 0.0, 0.5)

    def test_vad_local_dumps_split_chunks_in_split_mode(self) -> None:
        with TemporaryDirectory() as tmpdir:
            args = Namespace(
                input=str(JFK_WAV),
                config=None,
                start_s=0.0,
                end_s=1.0,
                threshold=None,
                min_speech_s=0.25,
                min_silence_s=0.20,
                speech_pad_s=0.05,
                merge_gap_s=0.10,
                channel=None,
                model_id="fake-model",
                dump_speech_dir=tmpdir,
                dump_audio_format="wav",
                split_every_minutes=10.0,
                split_radius_s=60.0,
                prefer_before_target=False,
                format="json",
            )

            with (
                patch("ja_media_apple.vad_cli.MlxAudioVadBackend", FakeBackendForCli),
                patch(
                    "ja_media_apple.vad_cli.plan_vad_splits",
                    return_value=[_jfk_chunk(0.0, 0.50), _jfk_chunk(0.50, 1.0)],
                ),
                redirect_stdout(StringIO()) as stdout,
            ):
                run_vad_local(args)

            payload = json.loads(stdout.getvalue())

            self.assertFalse(payload["speech_detected"])
            self.assertEqual(payload["speech"], [])
            self.assertEqual(payload["dumped_chunk_kind"], "split")
            self.assertEqual(len(payload["dumped_chunk_paths"]), 2)
            self.assertIn("_split_001_", Path(payload["dumped_chunk_paths"][0]).name)
            self.assertIn("_dur_000000500ms.", Path(payload["dumped_chunk_paths"][0]).name)

    def test_vad_local_uses_config_defaults_for_missing_flags(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            config_path.write_text(
                "[vad]\n"
                "threshold = 0.30\n"
                "min_speech_s = 0.11\n"
                "min_silence_s = 0.09\n"
                "speech_pad_s = 0.07\n"
                "merge_gap_s = 0.04\n",
                encoding="utf-8",
            )
            args = Namespace(
                input=str(JFK_WAV),
                config=str(config_path),
                start_s=0.0,
                end_s=1.0,
                threshold=None,
                min_speech_s=None,
                min_silence_s=None,
                speech_pad_s=None,
                merge_gap_s=None,
                channel=None,
                model_id="fake-model",
                dump_speech_dir=None,
                dump_audio_format="wav",
                split_every_minutes=None,
                split_radius_s=60.0,
                prefer_before_target=False,
                format="json",
            )

            FakeBackendForCli.instances = []
            with (
                patch("ja_media_apple.vad_cli.MlxAudioVadBackend", FakeBackendForCli),
                redirect_stdout(StringIO()) as stdout,
            ):
                run_vad_local(args)

            payload = json.loads(stdout.getvalue())
            options = FakeBackendForCli.instances[0].detect_calls[0]["options"]

            self.assertEqual(payload["vad_options"]["threshold"], 0.30)
            self.assertAlmostEqual(options.min_speech_s, 0.11)
            self.assertAlmostEqual(options.min_silence_s, 0.09)
            self.assertAlmostEqual(options.speech_pad_s, 0.07)
            self.assertAlmostEqual(options.merge_gap_s, 0.04)


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
