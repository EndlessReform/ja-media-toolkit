from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ja_media_frontend.subsync.audio_source import SubsyncAudioSelection
from ja_media_frontend.subsync.models import RemoteLookupState
from ja_media_frontend.subsync.startup import run_subsync_tui
from subsync_test_helpers import make_audio_source


class SubsyncVocalSeparationFlagTest(unittest.TestCase):
    def test_vocal_separation_disabled_by_default(self) -> None:
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
                    "ja_media_frontend.subsync.startup._prepare_vad_audio_source",
                    return_value=(None, "playback/VAD source: original audio"),
                ) as prepare,
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

        prepare.assert_called_once_with(source, vocal_separation=False)
        materialize.assert_called_once_with(source)
        self.assertIsNone(constructed["vad_audio_path"])

    def test_vocal_separation_flag_routes_to_stem(self) -> None:
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
                    "ja_media_frontend.subsync.startup._prepare_vad_audio_source",
                    return_value=(stem, "VAD/playback source: vocals stem"),
                ) as prepare,
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
                    vocal_separation=True,
                )

        prepare.assert_called_once_with(source, vocal_separation=True)
        materialize.assert_called_once_with(stem)
        self.assertEqual(constructed["audio_source"].source_path, stem)
        self.assertEqual(constructed["vad_audio_path"], stem)


if __name__ == "__main__":
    unittest.main()
