from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ja_media_frontend.subsync.audio_source import SubsyncAudioSelection
from ja_media_frontend.subsync.models import RemoteLookupState
from ja_media_frontend.subsync.startup import run_subsync_tui
from ja_media_frontend.subsync.vocal_separation import (
    SubsyncVocalSeparationConfig,
)
from subsync_test_helpers import make_audio_source


class SubsyncVocalSeparationConfigTest(unittest.TestCase):
    def test_missing_config_enables_demucs_by_default(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "missing.toml"

            with patch.dict(os.environ, {"JA_MEDIA_CONFIG": str(config_path)}):
                config = SubsyncVocalSeparationConfig.load()

        self.assertTrue(config.enabled)
        self.assertEqual(config.backend, "demucs")
        self.assertEqual(config.stem, "vocals")

    def test_loads_tui_local_vocal_separation_section(self) -> None:
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"
            cache_dir = Path(tmpdir) / "cache"
            config_path.write_text(
                "[subsync_tui.vocal_separation]\n"
                "enabled = false\n"
                "backend = \"demucs\"\n"
                "stem = \"vocals\"\n"
                f"cache_dir = \"{cache_dir}\"\n"
                "model = \"htdemucs_ft\"\n"
                "device = \"mps\"\n"
                "jobs = 2\n"
                "segment_s = 7.5\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"JA_MEDIA_CONFIG": str(config_path)}):
                config = SubsyncVocalSeparationConfig.load()

        self.assertFalse(config.enabled)
        self.assertEqual(config.cache_dir, cache_dir)
        self.assertEqual(config.model, "htdemucs_ft")
        self.assertEqual(config.device, "mps")
        self.assertEqual(config.jobs, 2)
        self.assertEqual(config.segment_s, 7.5)

    def test_tui_plays_the_separated_vocal_stem_when_enabled(self) -> None:
        constructed: dict[str, object] = {}

        class FakeApp:
            def __init__(self, **kwargs: object) -> None:
                constructed.update(kwargs)
                self.tracks: list[object] = []
                self.cue_indices: list[int] = []

            def sort_tracks_by_language(self) -> None:
                return None

            def run(self) -> None:
                return None

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            source = tmp / "episode.mkv"
            stem = tmp / "vocals.wav"
            source.write_bytes(b"source")
            stem.write_bytes(b"stem")

            with (
                patch(
                    "ja_media_frontend.subsync.startup.initial_remote_lookup_state",
                    return_value=RemoteLookupState(),
                ),
                patch(
                    "ja_media_frontend.subsync.startup.resolve_subsync_audio",
                    return_value=SubsyncAudioSelection(
                        playback_path=source,
                        promotion_target=source,
                        status="using supplied media audio",
                    ),
                ),
                patch(
                    "ja_media_frontend.subsync.startup.SubsyncVocalSeparationConfig.load",
                    return_value=SubsyncVocalSeparationConfig(),
                ),
                patch(
                    "ja_media_frontend.subsync.startup._prepare_vad_audio_source",
                    return_value=(stem, "VAD/playback source: vocals stem"),
                ),
                patch(
                    "ja_media_frontend.subsync.startup.materialize_audio",
                    side_effect=lambda path: make_audio_source(Path(path)),
                ) as materialize,
                patch("ja_media_frontend.subsync.tui.SubsyncTuiApp", FakeApp),
            ):
                run_subsync_tui(
                    source_path=str(source),
                    srt_inputs=[],
                    window_s=10.0,
                )

        materialize.assert_called_once_with(stem)
        self.assertEqual(constructed["audio_source"].source_path, stem)
        self.assertEqual(constructed["vad_audio_path"], stem)
        self.assertEqual(constructed["promotion_target"], source)


if __name__ == "__main__":
    unittest.main()
