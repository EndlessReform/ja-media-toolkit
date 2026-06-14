from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from ja_media_frontend.subsync_tui import (
    GAP_BLOCK,
    RemoteLookupState,
    SPAN_BLOCK,
    SubtitleTrack,
    SubsyncTuiApp,
    playback_range,
    resolve_srt_inputs,
    runtime_episode_number,
)
from ja_media_core.srt import read_srt


SRT_TEXT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "hello\n\n"
    "2\n"
    "00:00:05,000 --> 00:00:06,000\n"
    "world\n"
)


class SubsyncTuiTest(unittest.TestCase):
    def test_resolve_srt_inputs_expands_globs_and_deduplicates(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            first = tmp / "episode.a.srt"
            second = tmp / "episode.b.srt"
            first.write_text(SRT_TEXT, encoding="utf-8")
            second.write_text(SRT_TEXT, encoding="utf-8")

            paths = resolve_srt_inputs([str(tmp / "*.srt"), str(first)])

        self.assertEqual(
            [path.name for path in paths],
            ["episode.a.srt", "episode.b.srt"],
        )

    def test_resolve_srt_inputs_can_be_empty_for_remote_lookup(self) -> None:
        self.assertEqual(resolve_srt_inputs([], allow_empty=True), [])

    def test_runtime_episode_number_parses_media_filename_stem(self) -> None:
        self.assertEqual(runtime_episode_number("[Group] GANTZ.S01E16.1080p"), 16)

    def test_key_navigation_moves_current_cue(self) -> None:
        async def run_app() -> tuple[int, str]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mkv"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"")
                srt.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    source_path=media,
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    await pilot.press("l")
                    return app.cue_index, app.current_cue.text if app.current_cue else ""

        cue_index, cue_text = asyncio.run(run_app())

        self.assertEqual(cue_index, 1)
        self.assertEqual(cue_text, "world")

    def test_timeline_bar_uses_wide_terminal_space(self) -> None:
        async def run_app() -> int:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mkv"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"")
                srt.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    source_path=media,
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test(size=(180, 40)):
                    return app.timeline_bar_width()

        self.assertGreaterEqual(asyncio.run(run_app()), 160)

    def test_activity_bar_uses_blocks_and_visible_gaps(self) -> None:
        async def run_app() -> str:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mkv"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"")
                srt.write_text(
                    "1\n"
                    "00:00:00,000 --> 00:00:01,000\n"
                    "first\n\n"
                    "2\n"
                    "00:00:02,000 --> 00:00:03,000\n"
                    "second\n",
                    encoding="utf-8",
                )

                app = SubsyncTuiApp(
                    source_path=media,
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=4.0,
                )
                async with app.run_test():
                    return app._activity_bar(
                        width=8,
                        start_s=0.0,
                        end_s=4.0,
                        active_cue=None,
                    ).plain

        self.assertEqual(
            asyncio.run(run_app()),
            f"{SPAN_BLOCK}{SPAN_BLOCK}{GAP_BLOCK}{GAP_BLOCK}"
            f"{SPAN_BLOCK}{SPAN_BLOCK}{GAP_BLOCK}{GAP_BLOCK}",
        )

    def test_playback_range_uses_exact_cue_boundaries(self) -> None:
        cue = read_srt_from_text(
            "1\n"
            "00:00:00,250 --> 00:00:01,000\n"
            "border check\n"
        )[0]

        self.assertEqual(playback_range(cue), (0.25, 0.75))

    def test_space_starts_and_stops_ffplay_for_current_cue(self) -> None:
        async def run_app() -> tuple[list[str], bool]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mp3"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")
                fake_process = Mock()
                fake_process.poll.return_value = None

                app = SubsyncTuiApp(
                    source_path=media,
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                with patch(
                    "ja_media_frontend.subsync_tui.subprocess.Popen",
                    return_value=fake_process,
                ) as popen:
                    async with app.run_test() as pilot:
                        await pilot.press("space")
                        command = popen.call_args.args[0]
                        await pilot.press("space")
                        return command, fake_process.terminate.called

        command, terminated = asyncio.run(run_app())

        self.assertEqual(command[0], "ffplay")
        self.assertIn("-nodisp", command)
        self.assertIn("-autoexit", command)
        self.assertEqual(command[-5:-3], ["-ss", "1.000"])
        self.assertEqual(command[-3:-1], ["-t", "1.000"])
        self.assertTrue(command[-1].endswith("episode.mp3"))
        self.assertTrue(terminated)

    def test_cue_navigation_stops_active_playback(self) -> None:
        async def run_app() -> tuple[int, bool]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mp3"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")
                fake_process = Mock()
                fake_process.poll.return_value = None

                app = SubsyncTuiApp(
                    source_path=media,
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                with patch(
                    "ja_media_frontend.subsync_tui.subprocess.Popen",
                    return_value=fake_process,
                ):
                    async with app.run_test() as pilot:
                        await pilot.press("space")
                        await pilot.press("l")
                        return app.cue_index, fake_process.terminate.called

        cue_index, terminated = asyncio.run(run_app())

        self.assertEqual(cue_index, 1)
        self.assertTrue(terminated)

    def test_candidate_table_has_one_body_row_per_track(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.mp3"
            first = tmp / "episode.a.srt"
            second = tmp / "episode.b.srt"
            media.write_bytes(b"fake")
            first.write_text(SRT_TEXT, encoding="utf-8")
            second.write_text(SRT_TEXT, encoding="utf-8")
            app = SubsyncTuiApp(
                source_path=media,
                tracks=[
                    SubtitleTrack(first, read_srt(first)),
                    SubtitleTrack(second, read_srt(second)),
                ],
                initial_window_s=10.0,
            )

            table = app.render_candidates()

        self.assertEqual(len(table.rows), 2)

    def test_empty_tui_state_renders_without_tracks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            media = Path(tmpdir) / "episode.mp3"
            media.write_bytes(b"fake")
            app = SubsyncTuiApp(
                source_path=media,
                tracks=[],
                initial_window_s=10.0,
                remote_state=RemoteLookupState(
                    source="anilist",
                    external_id=395,
                    episode_number=16,
                ),
            )

            source = app.render_source().plain
            candidates = app.render_candidates()

        self.assertIn("anilist:395 ep:16", source)
        self.assertEqual(len(candidates.rows), 1)

    def test_fetch_remote_tracks_appends_srt_candidates(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.mp3"
            media.write_bytes(b"fake")
            app = SubsyncTuiApp(
                source_path=media,
                tracks=[],
                initial_window_s=10.0,
                remote_state=RemoteLookupState(
                    source="anilist",
                    external_id=395,
                    episode_number=16,
                ),
                download_dir=tmp,
            )

            fake_client = Mock()
            fake_client.anilist_episode_files.return_value.files = (
                {
                    "subtitle_id": "abc",
                    "repo_path": "subtitles/anime_tv/GANTZ/[Group] GANTZ - 16.srt",
                    "filename": "[Group] GANTZ - 16.srt",
                    "extension": ".srt",
                },
            )
            fake_client.file_content.return_value = SRT_TEXT.encode("utf-8")

            with patch(
                "ja_media_frontend.subsync_tui.HttpKitsunekkoSubtitlesClient",
                return_value=fake_client,
            ):
                added = app.fetch_remote_tracks()

        self.assertEqual(added, 1)
        self.assertEqual(len(app.tracks), 1)
        self.assertEqual(app.tracks[0].subtitle_id, "abc")
        self.assertEqual(app.tracks[0].cues[0].text, "hello")


def read_srt_from_text(text: str):
    from ja_media_core.srt import parse_srt

    return parse_srt(text)


if __name__ == "__main__":
    unittest.main()
