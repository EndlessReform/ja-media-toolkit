from __future__ import annotations

import unittest
from pathlib import Path

from ja_media_core.audio import AudioChunk, probe_audio_source, resolve_audio_source
from ja_media_core.vad import (
    SpeechSpan,
    VadOptions,
    VadTimeline,
    normalize_speech_spans,
    plan_vad_splits,
    speech_chunks_from_timeline,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
JFK_WAV = REPO_ROOT / "examples" / "input" / "jfk.wav"


class VadTimelineTest(unittest.TestCase):
    def test_timeline_rejects_overlapping_spans(self) -> None:
        chunk = _jfk_chunk(0.0, 4.0)

        with self.assertRaisesRegex(ValueError, "monotonic"):
            VadTimeline(
                chunk=chunk,
                speech=[
                    SpeechSpan(1.0, 2.0),
                    SpeechSpan(1.5, 3.0),
                ],
                backend="fake",
            )

    def test_normalize_clamps_pads_merges_and_filters(self) -> None:
        spans = normalize_speech_spans(
            [
                SpeechSpan(0.10, 0.12),
                SpeechSpan(0.50, 0.70),
                SpeechSpan(0.74, 0.90),
                SpeechSpan(2.70, 3.30),
            ],
            start_s=0.0,
            end_s=3.0,
            min_duration_s=0.25,
            merge_gap_s=0.10,
            pad_s=0.05,
        )

        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0].start_s, 0.45)
        self.assertAlmostEqual(spans[0].end_s, 0.95)
        self.assertAlmostEqual(spans[1].start_s, 2.65)
        self.assertAlmostEqual(spans[1].end_s, 3.0)

    def test_speech_chunks_preserve_source_coordinates_and_metadata(self) -> None:
        chunk = _jfk_chunk(0.0, 4.0)
        timeline = VadTimeline(
            chunk=chunk,
            speech=[SpeechSpan(1.0, 1.5, metadata={"speaker": 0})],
            backend="fake-vad",
            metadata={"model": "fake"},
        )

        chunks = speech_chunks_from_timeline(
            timeline,
            kind="utterance",
            metadata={"purpose": "benchmark"},
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].kind, "utterance")
        self.assertEqual(chunks[0].start_s, 1.0)
        self.assertEqual(chunks[0].end_s, 1.5)
        self.assertEqual(chunks[0].source_start_frame, 16_000)
        self.assertEqual(chunks[0].source_end_frame, 24_000)
        self.assertEqual(chunks[0].metadata["boundary_source"], "vad")
        self.assertEqual(chunks[0].metadata["vad_backend"], "fake-vad")
        self.assertEqual(chunks[0].metadata["purpose"], "benchmark")

    def test_plan_vad_splits_uses_nearest_qualifying_silence(self) -> None:
        chunk = _jfk_chunk(0.0, 10.0)
        backend = FakeBackend(
            [
                SpeechSpan(4.0, 4.6),
                SpeechSpan(5.4, 6.0),
            ]
        )

        chunks = plan_vad_splits(
            chunk,
            backend,
            every_s=5.0,
            search_radius_s=1.0,
            vad_options=VadOptions(min_silence_s=0.2),
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(backend.chunks), 1)
        self.assertAlmostEqual(backend.chunks[0].start_s, 4.0)
        self.assertAlmostEqual(backend.chunks[0].end_s, 6.0)
        self.assertEqual(chunks[0].end_s, 5.0)
        self.assertFalse(chunks[0].metadata["next_cut_fallback"])
        self.assertEqual(chunks[0].metadata["next_cut_silence_start_s"], 4.6)
        self.assertEqual(chunks[0].metadata["next_cut_silence_end_s"], 5.4)

    def test_plan_vad_splits_returns_fallback_when_no_silence_qualifies(self) -> None:
        chunk = _jfk_chunk(0.0, 10.0)
        backend = FakeBackend([SpeechSpan(4.0, 6.0)])

        chunks = plan_vad_splits(
            chunk,
            backend,
            every_s=5.0,
            search_radius_s=1.0,
            vad_options=VadOptions(min_silence_s=0.2),
        )

        self.assertEqual(chunks[0].end_s, 5.0)
        self.assertTrue(chunks[0].metadata["next_cut_fallback"])


class FakeBackend:
    name = "fake-vad"

    def __init__(self, speech: list[SpeechSpan]) -> None:
        self.speech = speech
        self.chunks: list[AudioChunk] = []

    def detect(
        self,
        chunks: list[AudioChunk],
        *,
        options: VadOptions | None = None,
    ) -> list[VadTimeline]:
        self.chunks.extend(chunks)
        return [
            VadTimeline(
                chunk=chunk,
                speech=[
                    span
                    for span in self.speech
                    if span.start_s >= chunk.start_s and span.end_s <= chunk.end_s
                ],
                backend=self.name,
            )
            for chunk in chunks
        ]


def _jfk_chunk(start_s: float, end_s: float) -> AudioChunk:
    source = resolve_audio_source(JFK_WAV, must_exist=True)
    audio_format = probe_audio_source(source)
    return AudioChunk(
        source=source,
        start_s=start_s,
        end_s=end_s,
        source_start_frame=round(start_s * audio_format.sample_rate_hz),
        source_end_frame=round(end_s * audio_format.sample_rate_hz),
        format=audio_format,
        kind="test",
    )


if __name__ == "__main__":
    unittest.main()
