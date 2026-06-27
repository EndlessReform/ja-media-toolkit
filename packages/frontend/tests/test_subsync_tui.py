from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ja_media_frontend.subsync.models import RemoteLookupState
from ja_media_frontend.subsync.tui import (
    SubtitleTrack,
    SubsyncTuiApp,
    playback_range,
    resolve_srt_inputs,
)
from ja_media_frontend.widgets.timeline import (
    GAP_BLOCK,
    SPAN_BLOCK,
    TimelineWidget,
)
from ja_media_core.subtitle_lid import (
    SubtitleLanguage,
    SubtitleLanguageIdConfig,
    analyze_subtitle_language,
)
from ja_media_core.subsync import infer_episode_number
from ja_media_core.transcripts import parse_ass, read_srt
from subsync_test_helpers import ASS_TEXT, SRT_TEXT, make_audio_source


class FakePlayer:
    def __init__(self) -> None:
        self.play_args: tuple[float, float] | None = None
        self.playing = False
        self.stop_called = False

    def play(self, start_s: float, duration_s: float) -> None:
        self.play_args = (start_s, duration_s)
        self.playing = True

    def stop(self) -> None:
        self.stop_called = True
        self.playing = False

    def is_playing(self) -> bool:
        return self.playing


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

    def test_infer_episode_number_parses_media_filename_stem(self) -> None:
        self.assertEqual(infer_episode_number("[Group] GANTZ.S01E16.1080p"), 16)

    def test_key_navigation_moves_current_cue(self) -> None:
        async def run_app() -> tuple[int, str]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mkv"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"")
                srt.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    await pilot.press("l")
                    return (
                        app.cue_index,
                        app.current_cue.text if app.current_cue else "",
                    )

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
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test(size=(180, 40)):
                    return app.query_one("#timeline", TimelineWidget).bar_width()

        self.assertGreaterEqual(asyncio.run(run_app()), 158)

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
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=4.0,
                )
                async with app.run_test():
                    timeline = app.query_one("#timeline", TimelineWidget)
                    return timeline.activity_bar(
                        label="Subtitle",
                        spans=app.track.cues,
                        width=8,
                        start_s=0.0,
                        end_s=4.0,
                        is_active=True,
                    ).plain

        self.assertEqual(
            asyncio.run(run_app()),
            "Subtitle   \u25cf "
            f"{SPAN_BLOCK}{SPAN_BLOCK}{GAP_BLOCK}{GAP_BLOCK}"
            f"{SPAN_BLOCK}{SPAN_BLOCK}{GAP_BLOCK}{GAP_BLOCK}",
        )

    def test_playback_range_uses_exact_cue_boundaries(self) -> None:
        cue = read_srt_from_text("1\n00:00:00,250 --> 00:00:01,000\nborder check\n")[0]

        self.assertEqual(playback_range(cue), (0.25, 0.75))

    def test_space_starts_and_stops_pcm_player_for_current_cue(self) -> None:
        async def run_app() -> tuple[float, float, bool]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mp3"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                fake_player = FakePlayer()
                app._player = fake_player
                async with app.run_test() as pilot:
                    await pilot.press("space")
                    assert fake_player.play_args is not None
                    start_s, duration_s = fake_player.play_args
                    await pilot.press("space")
                    return start_s, duration_s, fake_player.stop_called

        start_s, duration_s, stopped = asyncio.run(run_app())

        self.assertEqual(start_s, 1.0)
        self.assertEqual(duration_s, 1.0)
        self.assertTrue(stopped)

    def test_cue_navigation_stops_active_playback(self) -> None:
        async def run_app() -> tuple[int, bool]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mp3"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                fake_player = FakePlayer()
                fake_player.playing = True
                app._player = fake_player
                async with app.run_test() as pilot:
                    await pilot.press("l")
                    return app.cue_index, fake_player.stop_called

        cue_index, stopped = asyncio.run(run_app())

        self.assertEqual(cue_index, 1)
        self.assertTrue(stopped)

    def test_ctrl_c_copies_current_subtitle_instead_of_quitting(self) -> None:
        async def run_app() -> tuple[str, int]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mp3"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                with patch("ja_media_frontend.subsync.tui.write_clipboard") as copy:
                    async with app.run_test() as pilot:
                        await pilot.press("ctrl+c")
                        await pilot.press("l")
                        copied_text = copy.call_args.args[0]
                        return copied_text, app.cue_index

        copied_text, cue_index = asyncio.run(run_app())

        self.assertEqual(copied_text, "hello")
        self.assertEqual(cue_index, 1)

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
                audio_source=make_audio_source(media),
                tracks=[
                    SubtitleTrack(first, read_srt(first)),
                    SubtitleTrack(second, read_srt(second)),
                ],
                initial_window_s=10.0,
            )

            table = app.render_candidates()

        self.assertEqual(len(table.rows), 2)
        self.assertNotIn("LID", [column.header for column in table.columns])

    def test_opt_in_language_sort_places_japanese_before_bilingual_and_foreign(
        self,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.mp3"
            media.write_bytes(b"fake")
            config = SubtitleLanguageIdConfig(
                minimum_lines=1,
                minimum_characters=1,
                obvious_japanese_script_ratio=0.95,
            )

            japanese_cues = read_srt_from_text(
                "1\n00:00:01,000 --> 00:00:02,000\n今日は学校へ行きます。\n"
            )
            bilingual_cues = read_srt_from_text(
                "1\n00:00:01,000 --> 00:00:02,000\n"
                "今日は学校へ行きます。\nI am going to school today.\n"
            )
            foreign_cues = read_srt_from_text(
                "1\n00:00:01,000 --> 00:00:02,000\n"
                "This is an English subtitle candidate.\n"
            )

            japanese = SubtitleTrack(
                tmp / "japanese.srt",
                japanese_cues,
                language_analysis=analyze_subtitle_language(
                    japanese_cues,
                    config=config,
                    detector=lambda _: "ja",
                ),
            )
            bilingual = SubtitleTrack(
                tmp / "bilingual.srt",
                bilingual_cues,
                language_analysis=analyze_subtitle_language(
                    bilingual_cues,
                    config=config,
                    detector=lambda line: "ja" if "。" in line else "en",
                ),
            )
            foreign = SubtitleTrack(
                tmp / "foreign.srt",
                foreign_cues,
                language_analysis=analyze_subtitle_language(
                    foreign_cues,
                    config=config,
                    detector=lambda _: "en",
                ),
            )
            app = SubsyncTuiApp(
                audio_source=make_audio_source(media),
                tracks=[foreign, bilingual, japanese],
                initial_window_s=10.0,
                sort_by_language=True,
            )

            app.track_index = 1
            app.sort_tracks_by_language()

        self.assertEqual(
            [track.language_analysis.language for track in app.tracks],
            [
                SubtitleLanguage.JAPANESE,
                SubtitleLanguage.BILINGUAL,
                SubtitleLanguage.NON_JAPANESE,
            ],
        )
        self.assertIs(app.track, bilingual)

    def test_language_sort_is_disabled_by_default(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.mp3"
            media.write_bytes(b"fake")
            first = SubtitleTrack(tmp / "first.srt", [])
            second = SubtitleTrack(tmp / "second.srt", [])
            app = SubsyncTuiApp(
                audio_source=make_audio_source(media),
                tracks=[first, second],
                initial_window_s=10.0,
            )

            app.sort_tracks_by_language()

        self.assertEqual(app.tracks, [first, second])

    def test_empty_tui_state_renders_without_tracks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            media = Path(tmpdir) / "episode.mp3"
            media.write_bytes(b"fake")
            app = SubsyncTuiApp(
                audio_source=make_audio_source(media),
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

    def test_title_promotes_anilist_label_once_tracks_loaded(self) -> None:
        async def run_app() -> tuple[str, str]:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mp3"
                srt = tmp / "episode.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")
                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                    remote_state=RemoteLookupState(
                        source="anilist",
                        external_id=395,
                        episode_number=16,
                    ),
                )
                async with app.run_test() as pilot:
                    await pilot.pause()
                    return app.title, app.render_source().plain

        title, source = asyncio.run(run_app())
        self.assertEqual(title, "anilist:395 ep:16")
        self.assertNotIn("anilist:395 ep:16", source)

    def test_parse_ass_extracts_plain_dialogue_cues(self) -> None:
        cues = parse_ass(ASS_TEXT)

        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].start_s, 1.0)
        self.assertEqual(cues[0].end_s, 2.5)
        self.assertEqual(cues[0].text, "hello\nworld")

def read_srt_from_text(text: str):
    from ja_media_core.transcripts import parse_srt

    return parse_srt(text)


if __name__ == "__main__":
    unittest.main()
