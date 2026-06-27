from __future__ import annotations

from pathlib import Path

import numpy as np

from ja_media_frontend.audio import DEFAULT_PLAYBACK_SAMPLE_RATE, MaterializedAudio


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


def make_audio_source(path: Path, *, seconds: float = 10.0) -> MaterializedAudio:
    frame_count = round(seconds * DEFAULT_PLAYBACK_SAMPLE_RATE)
    return MaterializedAudio(
        source_path=path,
        sample_rate=DEFAULT_PLAYBACK_SAMPLE_RATE,
        samples=np.zeros((frame_count, 1), dtype=np.int16),
    )
