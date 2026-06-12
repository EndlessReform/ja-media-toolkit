from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException

from ja_media_frontend.subsync_reader import (
    discover_subtitle_file,
    parse_range_header,
)


class SubsyncReaderTest(unittest.TestCase):
    def test_discovers_exact_stem_srt(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.m4a"
            subtitle = tmp / "episode.srt"
            media.write_bytes(b"media")
            subtitle.write_text("", encoding="utf-8")

            self.assertEqual(discover_subtitle_file(media, sub_file=None), subtitle)

    def test_rejects_ambiguous_fuzzy_srt_matches(self) -> None:
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            media = tmp / "episode.m4a"
            media.write_bytes(b"media")
            (tmp / "episode.ja.srt").write_text("", encoding="utf-8")
            (tmp / "episode.en.srt").write_text("", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "Multiple SRT sidecars"):
                discover_subtitle_file(media, sub_file=None)

    def test_parse_range_header_supports_prefix_and_suffix_ranges(self) -> None:
        self.assertEqual(parse_range_header("bytes=10-19", file_size=100).length, 10)
        suffix = parse_range_header("bytes=-20", file_size=100)

        self.assertEqual((suffix.start, suffix.end), (80, 99))

    def test_parse_range_header_rejects_unsatisfiable_ranges(self) -> None:
        with self.assertRaises(HTTPException):
            parse_range_header("bytes=100-120", file_size=100)


if __name__ == "__main__":
    unittest.main()
