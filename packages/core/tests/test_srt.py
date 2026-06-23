from __future__ import annotations

import pytest

from ja_media_core.transcripts import (
    format_srt,
    format_srt_timestamp,
    parse_srt,
    parse_srt_timestamp,
    shift_srt_cues,
)


def test_parse_srt_cues_with_multiline_text_and_settings() -> None:
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

    assert len(cues) == 2
    assert cues[0].index == 1
    assert cues[0].start_s == pytest.approx(1.25)
    assert cues[0].end_s == pytest.approx(3.5)
    assert cues[0].text == "始めましょう\nReady?"
    assert cues[0].timing_settings == " position:50%"
    assert cues[1].start_s == pytest.approx(5.0)
    assert cues[1].end_s == pytest.approx(6.125)


def test_parse_srt_uses_document_order_when_index_is_missing() -> None:
    cues = parse_srt(
        "00:00:01,000 --> 00:00:02,000\n"
        "first\n\n"
        "7\n"
        "00:00:03,000 --> 00:00:04,000 position:50%\n"
        "second\n"
    )

    assert [cue.index for cue in cues] == [1, 7]
    assert cues[1].timing_settings == " position:50%"


def test_parse_srt_rejects_malformed_cues() -> None:
    with pytest.raises(ValueError, match="Malformed SRT"):
        parse_srt("1\nnot a timing line\ntext\n")


def test_format_srt_renumbers_cues_and_preserves_text() -> None:
    cues = parse_srt(
        "4\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "first\n\n"
        "9\n"
        "00:00:04,500 --> 00:00:05,000\n"
        "second\n"
    )

    assert format_srt(cues) == (
        "1\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "first\n\n"
        "2\n"
        "00:00:04,500 --> 00:00:05,000\n"
        "second\n"
    )


def test_shift_rejects_or_clamps_negative_timestamps() -> None:
    cues = parse_srt(
        "1\n"
        "00:00:01,000 --> 00:00:03,000\n"
        "hello\n"
    )

    with pytest.raises(ValueError, match="makes cue 1 negative"):
        shift_srt_cues(cues, -2.0)

    shifted = shift_srt_cues(cues, -2.0, negative="clamp")
    assert shifted[0].start_s == 0.0
    assert shifted[0].end_s == 1.0


def test_timestamp_round_trip_uses_milliseconds() -> None:
    assert parse_srt_timestamp("01:02:03,045") == pytest.approx(3723.045)
    assert format_srt_timestamp(3723.0454) == "01:02:03,045"
