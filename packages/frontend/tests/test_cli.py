from __future__ import annotations

import unittest

from ja_media_frontend.cli import build_parser


class FrontendCliTest(unittest.TestCase):
    def test_audio_library_ingest_parser(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "audio-library",
                "ingest",
                "--source",
                "/anime/Show",
                "--destination",
                "/derived",
                "--anilist",
                "154587",
                "--resume",
            ]
        )

        self.assertEqual(args.command, "audio-library")
        self.assertEqual(args.audio_library_command, "ingest")
        self.assertEqual(args.anilist, 154587)
        self.assertTrue(args.resume)

    def test_subsync_language_sort_is_opt_in(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["subsync", "tui", "episode.mkv"])

        self.assertFalse(args.sort_by_language)

    def test_subsync_tui_accepts_remote_lookup_without_srt_inputs(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "subsync",
                "tui",
                "episode.mkv",
                "--anilist",
                "395",
                "--episode",
                "16",
                "--fetch-subs",
                "--sort-by-language",
            ]
        )

        self.assertEqual(args.command, "subsync")
        self.assertEqual(args.subsync_command, "tui")
        self.assertEqual(args.srt, [])
        self.assertEqual(args.anilist, 395)
        self.assertIsNone(args.tvdb)
        self.assertEqual(args.episode, 16)
        self.assertTrue(args.fetch_subs)
        self.assertTrue(args.sort_by_language)


if __name__ == "__main__":
    unittest.main()
