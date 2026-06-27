from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import numpy as np

from ja_media_frontend.audio import (
    DEFAULT_PLAYBACK_SAMPLE_RATE,
    MaterializedAudio,
    MaterializedAudioPlayer,
    materialize_audio,
)


class FakeBackend:
    def __init__(self) -> None:
        self.play_args: tuple[object, int] | None = None
        self.stopped = False
        self.stream = Mock(active=True)

    def play(self, data, samplerate: int, *, blocking: bool = False):
        self.play_args = (data, samplerate)
        self.blocking = blocking

    def stop(self, *, ignore_errors: bool = True):
        self.stopped = True
        self.stream.active = False

    def get_stream(self):
        return self.stream


class AudioTest(unittest.TestCase):
    def test_materialize_audio_decodes_first_stream_to_mono_int16(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "episode.mkv"
            source.write_bytes(b"fake")
            pcm = b"\x00\x00\x01\x00"

            def fake_run(command, **kwargs):
                return Mock(returncode=0, stdout=pcm, stderr=b"")

            with (
                patch("ja_media_frontend.audio.shutil.which", return_value="ffmpeg"),
                patch(
                    "ja_media_frontend.audio.run_process",
                    side_effect=fake_run,
                ) as run,
            ):
                audio = materialize_audio(source)

            command = run.call_args.args[0]
            self.assertEqual(audio.source_path, source)
            self.assertEqual(audio.samples.tolist(), [[0], [1]])
            self.assertEqual(audio.samples.dtype, np.int16)
            self.assertEqual(audio.sample_rate, DEFAULT_PLAYBACK_SAMPLE_RATE)
            self.assertEqual(command[:6], [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source)
            ])
            self.assertIn("0:a:0", command)
            self.assertIn("pcm_s16le", command)
            self.assertIn("s16le", command)
            self.assertIn("pipe:1", command)

    def test_materialize_audio_bails_loudly_when_ffmpeg_is_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "episode.mkv"
            with patch("ja_media_frontend.audio.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg not found"):
                    materialize_audio(source)

    def test_materialize_audio_includes_ffmpeg_stderr_on_failure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "episode.mkv"

            def fake_run(command, **kwargs):
                return Mock(returncode=1, stdout=b"", stderr=b"Stream map failed")

            with (
                patch("ja_media_frontend.audio.shutil.which", return_value="ffmpeg"),
                patch(
                    "ja_media_frontend.audio.run_process",
                    side_effect=fake_run,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "Stream map failed"):
                    materialize_audio(source)

    def test_player_uses_sounddevice_convenience_playback_and_stop(self) -> None:
        audio = MaterializedAudio(
            source_path=Path("episode.mkv"),
            sample_rate=10,
            samples=np.arange(20, dtype=np.int16).reshape((-1, 1)),
        )
        backend = FakeBackend()
        player = MaterializedAudioPlayer(audio, backend=backend)

        player.play(0.2, 0.3)

        assert backend.play_args is not None
        samples, sample_rate = backend.play_args
        np.testing.assert_array_equal(samples, [[2], [3], [4]])
        self.assertEqual(sample_rate, 10)
        self.assertFalse(backend.blocking)
        self.assertTrue(player.is_playing())

        player.stop()

        self.assertTrue(backend.stopped)
        self.assertFalse(player.is_playing())
