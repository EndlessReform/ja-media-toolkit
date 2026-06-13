from __future__ import annotations

import unittest

from ja_media_transcripts.srt import (
    format_srt,
    format_srt_timestamp,
    parse_srt,
    parse_srt_timestamp,
    shift_srt_cues,
)


class SrtParsingTest(unittest.TestCase):
    def test_parse_srt_cues_with_multiline_text_and_settings(self) -> None:
        cues = parse_srt(
            "\ufeff1\r\n"
            "00:00:01,250 --> 00:00:03,500 position:50%\r\n"
            "始めましょう\r\n"
            "Ready?\r\n"
            "\r\n"
            "2\r\n"
            "00:00:05.000 --> 00:00:06.125\r\n"
            "次です\r\n"
        )

        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].index, 1)
        self.assertAlmostEqual(cues[0].start_s, 1.25)
        self.assertAlmostEqual(cues[0].end_s, 3.5)
        self.assertEqual(cues[0].text, "始めましょう\nReady?")
        self.assertEqual(cues[0].timing_settings, " position:50%")
        self.assertAlmostEqual(cues[1].start_s, 5.0)
        self.assertAlmostEqual(cues[1].end_s, 6.125)

    def test_format_srt_renumbers_cues_and_preserves_text(self) -> None:
        cues = parse_srt(
            "4\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "first\n\n"
            "9\n"
            "00:00:04,500 --> 00:00:05,000\n"
            "second\n"
        )

        self.assertEqual(
            format_srt(cues),
            "1\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "first\n\n"
            "2\n"
            "00:00:04,500 --> 00:00:05,000\n"
            "second\n",
        )

    def test_shift_rejects_or_clamps_negative_timestamps(self) -> None:
        cues = parse_srt(
            "1\n"
            "00:00:01,000 --> 00:00:03,000\n"
            "hello\n"
        )

        with self.assertRaisesRegex(ValueError, "makes cue 1 negative"):
            shift_srt_cues(cues, -2.0)

        shifted = shift_srt_cues(cues, -2.0, negative="clamp")
        self.assertEqual(shifted[0].start_s, 0.0)
        self.assertEqual(shifted[0].end_s, 1.0)

    def test_timestamp_round_trip_uses_milliseconds(self) -> None:
        self.assertAlmostEqual(parse_srt_timestamp("01:02:03,045"), 3723.045)
        self.assertEqual(format_srt_timestamp(3723.0454), "01:02:03,045")


if __name__ == "__main__":
    unittest.main()
