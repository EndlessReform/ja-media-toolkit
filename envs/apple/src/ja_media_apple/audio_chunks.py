from __future__ import annotations

from ja_media_core.audio import AudioChunk


def select_audio_chunk(
    full_chunk: AudioChunk,
    *,
    start_s: float,
    end_s: float | None,
) -> AudioChunk:
    """Return a source-clock subchunk selected from a local full-file chunk."""

    if full_chunk.format is None or full_chunk.format.duration_s is None:
        raise ValueError("Cannot select a local audio chunk without known duration")
    selected_end_s = full_chunk.format.duration_s if end_s is None else end_s
    if start_s < 0:
        raise ValueError("Chunk start must be non-negative")
    if selected_end_s <= start_s:
        raise ValueError("Chunk end must be after start")
    if selected_end_s > full_chunk.format.duration_s:
        raise ValueError("Chunk end is beyond the source duration")

    sample_rate_hz = full_chunk.format.sample_rate_hz
    return AudioChunk(
        source=full_chunk.source,
        start_s=start_s,
        end_s=selected_end_s,
        source_start_frame=round(start_s * sample_rate_hz),
        source_end_frame=round(selected_end_s * sample_rate_hz),
        format=full_chunk.format,
        kind=full_chunk.kind,
        metadata=dict(full_chunk.metadata),
    )
