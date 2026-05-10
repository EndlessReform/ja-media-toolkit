from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from ja_media_core.audio import (
    AudioChunk,
    full_audio_chunk,
    materialize_audio_chunk,
    probe_audio_source,
    resolve_audio_source,
    write_audio_chunk,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
JFK_WAV = REPO_ROOT / "examples" / "input" / "jfk.wav"


class AudioSourceResolutionTest(unittest.TestCase):
    def test_unprefixed_locator_defaults_to_client_local(self) -> None:
        source = resolve_audio_source(
            "./examples/input/jfk.wav",
            base_dir=REPO_ROOT,
            must_exist=True,
        )

        self.assertEqual(source.kind, "client-local")
        self.assertEqual(source.id, "jfk")
        self.assertEqual(source.locator, str(JFK_WAV.resolve()))
        self.assertEqual(source.metadata["input_locator"], "./examples/input/jfk.wav")

    def test_s3_locator_is_preserved_without_local_resolution(self) -> None:
        source = resolve_audio_source("s3://media-bucket/audio/jfk.wav")

        self.assertEqual(source.kind, "s3")
        self.assertEqual(source.id, "jfk")
        self.assertEqual(source.locator, "s3://media-bucket/audio/jfk.wav")
        self.assertEqual(source.metadata["bucket"], "media-bucket")
        self.assertEqual(source.metadata["key"], "audio/jfk.wav")

    def test_unknown_prefixed_locator_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported audio locator scheme"):
            resolve_audio_source("https://example.com/audio.wav")


class LocalAudioIngestTest(unittest.TestCase):
    def test_probe_jfk_wav(self) -> None:
        source = resolve_audio_source(JFK_WAV, must_exist=True)
        audio_format = probe_audio_source(source)

        self.assertEqual(audio_format.sample_rate_hz, 16_000)
        self.assertEqual(audio_format.channels, 1)
        self.assertEqual(audio_format.codec, "pcm_16")
        self.assertEqual(audio_format.container, "wav")
        self.assertIsNotNone(audio_format.frame_count)
        self.assertGreater(audio_format.duration_s or 0.0, 0.0)
        self.assertIn("format_info", audio_format.metadata)

    def test_materialize_short_jfk_chunk_as_float32_frames_by_channels(self) -> None:
        source = resolve_audio_source(JFK_WAV, must_exist=True)
        audio_format = probe_audio_source(source)
        chunk = AudioChunk(
            source=source,
            start_s=0.0,
            end_s=1.0,
            format=audio_format,
            kind="smoke_test",
            metadata={"purpose": "jfk materialization"},
        )

        materialized = materialize_audio_chunk(chunk)

        self.assertEqual(materialized.sample_rate_hz, 16_000)
        self.assertEqual(materialized.source_sample_rate_hz, 16_000)
        self.assertEqual(materialized.channels, 1)
        self.assertEqual(materialized.samples.shape, (16_000, 1))
        self.assertEqual(str(materialized.samples.dtype), "float32")
        self.assertGreaterEqual(float(materialized.samples.min()), -1.0)
        self.assertLessEqual(float(materialized.samples.max()), 1.0)
        self.assertEqual(materialized.chunk.source_start_frame, 0)
        self.assertEqual(materialized.chunk.source_end_frame, 16_000)

    def test_full_audio_chunk_uses_probe_duration_and_frame_count(self) -> None:
        source = resolve_audio_source(JFK_WAV, must_exist=True)
        audio_format = probe_audio_source(source)
        chunk = full_audio_chunk(source, audio_format, metadata={"purpose": "full"})

        self.assertEqual(chunk.start_s, 0.0)
        self.assertEqual(chunk.source_start_frame, 0)
        self.assertEqual(chunk.source_end_frame, audio_format.frame_count)
        self.assertEqual(chunk.metadata["purpose"], "full")

    def test_write_audio_chunk_exports_flac(self) -> None:
        source = resolve_audio_source(JFK_WAV, must_exist=True)
        audio_format = probe_audio_source(source)
        chunk = AudioChunk(
            source=source,
            start_s=0.0,
            end_s=1.0,
            format=audio_format,
            kind="export_test",
        )

        with TemporaryDirectory() as tmpdir:
            output_path = write_audio_chunk(
                chunk,
                Path(tmpdir) / "chunk.flac",
                format="FLAC",
            )
            exported = resolve_audio_source(output_path, must_exist=True)
            exported_format = probe_audio_source(exported)

        self.assertEqual(output_path.name, "chunk.flac")
        self.assertEqual(exported_format.sample_rate_hz, 16_000)
        self.assertEqual(exported_format.channels, 1)
        self.assertAlmostEqual(exported_format.duration_s or 0.0, 1.0)


if __name__ == "__main__":
    unittest.main()
