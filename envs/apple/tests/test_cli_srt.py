from __future__ import annotations

from ja_media_apple.cli import _payload_to_srt


def test_payload_to_srt_uses_rejoined_segments() -> None:
    payload = {
        "source": {"locator": "/media/episode.wav"},
        "rejoined": {
            "segments": [
                {"start_s": 1.25, "end_s": 2.5, "text": "  hello  "},
                {"start_s": 3.0, "end_s": 3.0, "text": "empty duration"},
                {"start_s": 4.0, "end_s": 5.125, "text": "world"},
            ]
        },
    }

    assert _payload_to_srt(payload) == (
        "1\n"
        "00:00:01,250 --> 00:00:02,500\n"
        "hello\n\n"
        "2\n"
        "00:00:04,000 --> 00:00:05,125\n"
        "world\n"
    )
