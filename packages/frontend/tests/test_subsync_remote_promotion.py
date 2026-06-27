from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from ja_media_core.transcripts import read_srt
from ja_media_frontend.subsync.dialogs import ConfirmOverwriteModal
from ja_media_frontend.subsync.models import RemoteLookupState
from ja_media_frontend.subsync.tui import SubtitleTrack, SubsyncTuiApp
from subsync_test_helpers import ASS_TEXT, SRT_TEXT, make_audio_source


class SubsyncRemotePromotionTest(unittest.TestCase):
    def test_fetch_remote_tracks_appends_srt_candidates(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.mp3"
            media.write_bytes(b"fake")
            app = _remote_app(tmp, media)

            fake_client = Mock()
            fake_client.anilist_episode_files.return_value.files = (
                {
                    "subtitle_id": "abc",
                    "repo_path": "subtitles/anime_tv/GANTZ/[Group] GANTZ - 16.srt",
                    "filename": "[Group] GANTZ - 16.srt",
                    "extension": "srt",
                },
            )
            fake_client.file_content.return_value = SRT_TEXT.encode("utf-8")

            with patch(
                "ja_media_frontend.subsync.tui.HttpKitsunekkoSubtitlesClient",
                return_value=fake_client,
            ):
                added, first_idx = app.fetch_remote_tracks()

        self.assertEqual(added, 1)
        self.assertEqual(first_idx, 0)
        self.assertEqual(len(app.tracks), 1)
        self.assertEqual(app.tracks[0].subtitle_id, "abc")
        self.assertEqual(app.tracks[0].cues[0].text, "hello")

    def test_fetch_remote_tracks_converts_ass_candidates_to_srt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.mp3"
            media.write_bytes(b"fake")
            app = _remote_app(tmp, media)

            fake_client = Mock()
            fake_client.anilist_episode_files.return_value.files = (
                {
                    "subtitle_id": "ass-id",
                    "repo_path": "subtitles/anime_tv/GANTZ/[Group] GANTZ - 16.ass",
                    "filename": "[Group] GANTZ - 16.ass",
                    "extension": "ass",
                },
            )
            fake_client.file_content.return_value = ASS_TEXT.encode("utf-8")

            with patch(
                "ja_media_frontend.subsync.tui.HttpKitsunekkoSubtitlesClient",
                return_value=fake_client,
            ):
                added, first_idx = app.fetch_remote_tracks()

        self.assertEqual(added, 1)
        self.assertEqual(first_idx, 0)
        self.assertEqual(app.tracks[0].path.suffix, ".srt")
        self.assertEqual(app.tracks[0].cues[0].text, "hello\nworld")

    def test_fetch_remote_tracks_or_exit_defers_manual_pick_on_episode_404(
        self,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.mp3"
            media.write_bytes(b"fake")
            app = _remote_app(tmp, media)

            fake_client = Mock()
            fake_client.anilist_episode_files.side_effect = RuntimeError(
                "Kitsunekko subtitles request failed: 404"
            )
            fake_client.anilist_files.return_value.files = (
                {
                    "subtitle_id": "abc",
                    "repo_path": "subtitles/anime_tv/GANTZ/[Group] GANTZ - 16.srt",
                    "filename": "[Group] GANTZ - 16.srt",
                    "extension": "srt",
                },
            )

            with patch(
                "ja_media_frontend.subsync.tui.HttpKitsunekkoSubtitlesClient",
                return_value=fake_client,
            ):
                app.fetch_remote_tracks_or_exit()

        self.assertEqual(app.remote_state.status, "episode not found; pick manually")
        self.assertIn("full series list", app._pending_remote_file_picker_message)

    def test_promote_copies_track_to_stem_sidecar(self) -> None:
        async def run_app() -> str:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media, srt = _write_media_and_srt(tmp)

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    app.action_promote()
                    await pilot.pause()
                    dest = tmp / "episode.srt"
                    self.assertTrue(dest.exists())
                    return dest.read_text(encoding="utf-8")

        content = asyncio.run(run_app())
        self.assertEqual(content, SRT_TEXT)

    def test_promote_does_not_preserve_metadata(self) -> None:
        async def run_app() -> str:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media, srt = _write_media_and_srt(tmp)

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    with patch(
                        "ja_media_frontend.subsync.tui.shutil.copy2",
                        side_effect=PermissionError("metadata denied"),
                    ) as copy2:
                        app.action_promote()
                        await pilot.pause()
                        copy2.assert_not_called()
                    return (tmp / "episode.srt").read_text(encoding="utf-8")

        content = asyncio.run(run_app())
        self.assertEqual(content, SRT_TEXT)

    def test_promote_selected_sidecar_is_noop(self) -> None:
        async def run_app() -> bool:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mp4"
                sidecar = tmp / "episode.srt"
                media.write_bytes(b"fake")
                sidecar.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(sidecar, read_srt(sidecar))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    await pilot.press("p")
                    await pilot.pause()
                    return not isinstance(app.screen, ConfirmOverwriteModal)

        self.assertTrue(asyncio.run(run_app()))

    def test_promote_no_tracks_shows_error(self) -> None:
        async def run_app() -> str:
            with TemporaryDirectory() as tmpdir:
                media = Path(tmpdir) / "episode.mp4"
                media.write_bytes(b"fake")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    await pilot.press("p")
                    await pilot.pause()
                    return app.render_help()

        asyncio.run(run_app())  # should not raise

    def test_promote_existing_sidecar_triggers_confirm_modal(self) -> None:
        async def run_app() -> bool:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media, srt = _write_media_and_srt(tmp)
                existing = tmp / "episode.srt"
                existing.write_text("OLD CONTENT", encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    await pilot.press("p")
                    await pilot.pause()
                    modal = app.screen
                    self.assertIsInstance(modal, ConfirmOverwriteModal)
                    await pilot.press("escape")
                    await pilot.pause()
                    return existing.read_text(encoding="utf-8") == "OLD CONTENT"

        preserved = asyncio.run(run_app())
        self.assertTrue(preserved)


def _remote_app(tmp: Path, media: Path) -> SubsyncTuiApp:
    return SubsyncTuiApp(
        audio_source=make_audio_source(media),
        tracks=[],
        initial_window_s=10.0,
        remote_state=RemoteLookupState(
            source="anilist",
            external_id=395,
            episode_number=16,
        ),
        download_dir=tmp,
    )


def _write_media_and_srt(tmp: Path) -> tuple[Path, Path]:
    media = tmp / "episode.mp4"
    srt = tmp / "episode.ja.srt"
    media.write_bytes(b"fake")
    srt.write_text(SRT_TEXT, encoding="utf-8")
    return media, srt


if __name__ == "__main__":
    unittest.main()
