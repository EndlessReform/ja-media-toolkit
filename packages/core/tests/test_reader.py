from __future__ import annotations

import unittest
from pathlib import Path

from ja_media_core.reader import reader_session_from_cues
from ja_media_core.transcripts import parse_srt


class ReaderSessionTest(unittest.TestCase):
    def test_reader_session_builds_media_and_subtitle_tracks(self) -> None:
        cues = parse_srt(
            "1\n"
            "00:00:01,000 --> 00:00:02,500\n"
            "first\n\n"
            "2\n"
            "00:00:04,000 --> 00:00:05,000\n"
            "second\n"
        )

        session = reader_session_from_cues(
            media_path=Path("/tmp/episode.m4a"),
            subtitle_path=Path("/tmp/episode.srt"),
            cues=cues,
            media_duration_s=10.0,
        )

        self.assertEqual([track.kind for track in session.timeline_tracks], ["media", "subtitle"])
        self.assertEqual(session.timeline_tracks[0].spans[0].end_s, 10.0)
        self.assertEqual(session.timeline_tracks[1].spans[1].cue_index, 1)
        self.assertEqual(session.timeline_end_s, 10.0)


if __name__ == "__main__":
    unittest.main()
