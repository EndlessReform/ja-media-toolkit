from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ja_media_frontend.audio import MaterializedAudio, materialize_audio
from ja_media_frontend.subsync.audio_source import resolve_subsync_audio


@dataclass(frozen=True)
class ReviewAudio:
    """Decoded review audio plus a human-readable resolution status."""

    materialized: MaterializedAudio | None
    status: str


def load_review_audio(
    *,
    anilist_id: int,
    episode_number: int,
    manual_audio: Path | None,
    audio_profile: str,
) -> ReviewAudio:
    """Resolve and decode audio for a review episode, tolerating misses."""

    if manual_audio is not None:
        try:
            return ReviewAudio(
                materialize_audio(manual_audio.expanduser().resolve()),
                "using manually assigned audio",
            )
        except RuntimeError as exc:
            return ReviewAudio(None, f"manual audio decode unavailable: {exc}")

    try:
        selection = resolve_subsync_audio(
            None,
            anilist_id=anilist_id,
            episode_number=episode_number,
            profile=audio_profile,
        )
    except ValueError as exc:
        return ReviewAudio(None, f"audio unavailable: {exc}")

    try:
        return ReviewAudio(
            materialize_audio(selection.playback_path),
            selection.status,
        )
    except RuntimeError as exc:
        return ReviewAudio(None, f"audio decode unavailable: {exc}")


def prefetch_neighbor_audio(
    *,
    anilist_id: int,
    episode_number: int,
    audio_profile: str,
    radius: int = 1,
) -> None:
    """Warm the derived-audio cache for nearby episodes when the service has it."""

    for offset in range(-radius, radius + 1):
        neighbor = episode_number + offset
        if offset == 0 or neighbor <= 0:
            continue
        try:
            resolve_subsync_audio(
                None,
                anilist_id=anilist_id,
                episode_number=neighbor,
                profile=audio_profile,
            )
        except ValueError:
            continue
