from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ja_media_apple.vocal_separation import DemucsVocalSeparationBackend
from ja_media_core.audio import AudioChunk, probe_audio_source, resolve_audio_source
from ja_media_core.vocal_separation import VocalSeparationOptions


REPO_ROOT = Path(__file__).resolve().parents[3]
JFK_WAV = REPO_ROOT / "examples" / "input" / "jfk.wav"


class DemucsVocalSeparationBackendTest(unittest.TestCase):
    def test_separate_runs_demucs_and_reuses_cached_stem(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], *, check: bool) -> object:
            calls.append(command)
            output_root = Path(command[command.index("-o") + 1])
            stem_dir = output_root / "fake-demucs" / JFK_WAV.stem
            stem_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(JFK_WAV, stem_dir / "vocals.wav")
            return object()

        with TemporaryDirectory() as tmpdir:
            backend = DemucsVocalSeparationBackend(
                model="fake-demucs",
                device="cpu",
                jobs=2,
                segment_s=5.0,
                command="fake-demucs",
            )
            options = VocalSeparationOptions(cache_dir=Path(tmpdir))

            with (
                patch(
                    "ja_media_apple.vocal_separation.shutil.which",
                    return_value="/bin/fake-demucs",
                ),
                patch("ja_media_apple.vocal_separation.run_process", fake_run),
            ):
                first = backend.separate([_jfk_chunk()], options=options)[0]
                second = backend.separate([_jfk_chunk()], options=options)[0]
                self.assertTrue(Path(first.stem_chunk.source.locator).is_file())

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(len(calls), 1)
        self.assertIn("--two-stems", calls[0])
        self.assertIn("vocals", calls[0])
        self.assertIn("-d", calls[0])
        self.assertEqual(first.stem_chunk.kind, "vocal_stem")

    def test_real_demucs_smoke_when_requested(self) -> None:
        if os.environ.get("JA_MEDIA_RUN_DEMUCS_SMOKE") != "1":
            self.skipTest("set JA_MEDIA_RUN_DEMUCS_SMOKE=1 to run real Demucs")
        audio_path = os.environ.get("JA_MEDIA_DEMUCS_SMOKE_AUDIO")
        if not audio_path:
            self.skipTest("set JA_MEDIA_DEMUCS_SMOKE_AUDIO to a short audio file")

        source = resolve_audio_source(audio_path, must_exist=True)
        audio_format = probe_audio_source(source)
        assert audio_format.duration_s is not None
        chunk = AudioChunk(
            source=source,
            start_s=0.0,
            end_s=audio_format.duration_s,
            source_start_frame=0,
            source_end_frame=audio_format.frame_count,
            format=audio_format,
            kind="demucs_smoke",
        )

        with TemporaryDirectory() as tmpdir:
            backend = DemucsVocalSeparationBackend(
                model="htdemucs",
                device="cpu",
                jobs=1,
                segment_s=6.0,
            )
            result = backend.separate(
                [chunk],
                options=VocalSeparationOptions(cache_dir=Path(tmpdir)),
            )[0]

            stem_path = Path(result.stem_chunk.source.locator)
            self.assertTrue(stem_path.is_file())
            self.assertEqual(result.stem_chunk.kind, "vocal_stem")
            self.assertGreater(result.stem_chunk.duration_s, 0.0)


def _jfk_chunk() -> AudioChunk:
    source = resolve_audio_source(JFK_WAV, must_exist=True)
    audio_format = probe_audio_source(source)
    assert audio_format.duration_s is not None
    return AudioChunk(
        source=source,
        start_s=0.0,
        end_s=audio_format.duration_s,
        source_start_frame=0,
        source_end_frame=audio_format.frame_count,
        format=audio_format,
        kind="test",
    )


if __name__ == "__main__":
    unittest.main()
