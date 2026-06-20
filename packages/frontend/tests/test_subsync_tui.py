from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from ja_media_frontend.subsync_tui import (
    ConfirmOverwriteModal,
    GAP_BLOCK,
    PCM_CHANNELS,
    PCM_SAMPLE_RATE,
    PCM_SAMPLE_WIDTH_BYTES,
    PcmAudioSource,
    RemoteLookupState,
    SPAN_BLOCK,
    SubtitleTrack,
    SubsyncTuiApp,
    load_pcm_audio_source,
    parse_ass,
    playback_range,
    resolve_srt_inputs,
    runtime_episode_number,
)
from ja_media_core.subtitle_lid import (
    SubtitleLanguage,
    SubtitleLanguageIdConfig,
    analyze_subtitle_language,
)
from ja_media_core.transcripts import read_srt


SRT_TEXT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "hello\n\n"
    "2\n"
    "00:00:05,000 --> 00:00:06,000\n"
    "world\n"
)

ASS_TEXT = (
    "[Script Info]\n"
    "Title: example\n\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\i1}hello\\Nworld\n"
)


def make_audio_source(path: Path, *, seconds: float = 10.0) -> PcmAudioSource:
    frame_count = round(seconds * PCM_SAMPLE_RATE)
    return PcmAudioSource(
        source_path=path,
        sample_rate=PCM_SAMPLE_RATE,
        channels=PCM_CHANNELS,
        sample_width_bytes=PCM_SAMPLE_WIDTH_BYTES,
        pcm=b"\x00\x00" * frame_count,
    )


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

    def test_runtime_episode_number_parses_media_filename_stem(self) -> None:
        self.assertEqual(runtime_episode_number("[Group] GANTZ.S01E16.1080p"), 16)

    def test_load_pcm_audio_source_decodes_first_audio_stream(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "episode.mkv"
            source.write_bytes(b"fake")
            pcm = b"\x00\x00\x01\x00"

            with (
                patch(
                    "ja_media_frontend.subsync_tui.shutil.which", return_value="ffmpeg"
                ),
                patch(
                    "ja_media_frontend.subsync_tui.subprocess.run",
                    return_value=Mock(returncode=0, stdout=pcm, stderr=b""),
                ) as run,
            ):
                audio = load_pcm_audio_source(source)

            command = run.call_args.args[0]
            self.assertEqual(audio.source_path, source)
            self.assertEqual(audio.pcm, pcm)
            self.assertEqual(audio.sample_rate, PCM_SAMPLE_RATE)
            self.assertEqual(audio.channels, PCM_CHANNELS)
            self.assertEqual(
                command[:6],
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source)],
            )
            self.assertIn("-map", command)
            self.assertIn("0:a:0", command)
            self.assertIn("pcm_s16le", command)
            self.assertEqual(command[-2:], ["s16le", "pipe:1"])

    def test_load_pcm_audio_source_bails_loudly_when_ffmpeg_is_missing(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "episode.mkv"
            source.write_bytes(b"fake")

            with patch("ja_media_frontend.subsync_tui.shutil.which", return_value=None):
                with self.assertRaisesRegex(SystemExit, "ffmpeg not found"):
                    load_pcm_audio_source(source)

    def test_load_pcm_audio_source_includes_ffmpeg_stderr_on_failure(self) -> None:
        with TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "episode.mkv"
            source.write_bytes(b"fake")

            with (
                patch(
                    "ja_media_frontend.subsync_tui.shutil.which", return_value="ffmpeg"
                ),
                patch(
                    "ja_media_frontend.subsync_tui.subprocess.run",
                    return_value=Mock(
                        returncode=1, stdout=b"", stderr=b"Stream map failed"
                    ),
                ),
            ):
                with self.assertRaisesRegex(SystemExit, "Stream map failed"):
                    load_pcm_audio_source(source)

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
                    audio_source=make_audio_source(media),
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
                with patch("ja_media_frontend.subsync_tui.write_clipboard") as copy:
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
        self.assertEqual([column.header for column in table.columns][2], "LID")

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

    def test_fetch_remote_tracks_appends_srt_candidates(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.mp3"
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
                download_dir=tmp,
            )

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
                "ja_media_frontend.subsync_tui.HttpKitsunekkoSubtitlesClient",
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
            app = SubsyncTuiApp(
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
                "ja_media_frontend.subsync_tui.HttpKitsunekkoSubtitlesClient",
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
            app = SubsyncTuiApp(
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
                "ja_media_frontend.subsync_tui.HttpKitsunekkoSubtitlesClient",
                return_value=fake_client,
            ):
                app.fetch_remote_tracks_or_exit()

        self.assertEqual(app.remote_state.status, "episode not found; pick manually")
        self.assertIn("full series list", app._pending_remote_file_picker_message)

    def test_parse_ass_extracts_plain_dialogue_cues(self) -> None:
        cues = parse_ass(ASS_TEXT)

        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].start_s, 1.0)
        self.assertEqual(cues[0].end_s, 2.5)
        self.assertEqual(cues[0].text, "hello\nworld")

    def test_promote_copies_track_to_stem_sidecar(self) -> None:
        async def run_app() -> str:
            with TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                media = tmp / "episode.mp4"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")

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
                media = tmp / "episode.mp4"
                srt = tmp / "episode.ja.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    with patch(
                        "ja_media_frontend.subsync_tui.shutil.copy2",
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
                media = tmp / "episode.mp4"
                srt = tmp / "episode.ja.srt"
                existing = tmp / "episode.srt"
                media.write_bytes(b"fake")
                srt.write_text(SRT_TEXT, encoding="utf-8")
                existing.write_text("OLD CONTENT", encoding="utf-8")

                app = SubsyncTuiApp(
                    audio_source=make_audio_source(media),
                    tracks=[SubtitleTrack(srt, read_srt(srt))],
                    initial_window_s=10.0,
                )
                async with app.run_test() as pilot:
                    await pilot.press("p")
                    await pilot.pause()
                    # Modal should be on top
                    modal = app.screen
                    self.assertIsInstance(modal, ConfirmOverwriteModal)
                    # Press "No" (dismiss without overwriting)
                    await pilot.press("escape")
                    await pilot.pause()
                    return existing.read_text(encoding="utf-8") == "OLD CONTENT"

        preserved = asyncio.run(run_app())
        self.assertTrue(preserved)


def read_srt_from_text(text: str):
    from ja_media_core.transcripts import parse_srt

    return parse_srt(text)


if __name__ == "__main__":
    unittest.main()
