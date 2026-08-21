"""Compatibility exports for the shared local playback implementation."""

from ja_media_media.playback import (
    DEFAULT_PLAYBACK_CHANNELS,
    DEFAULT_PLAYBACK_SAMPLE_RATE,
    MaterializedAudio,
    MaterializedAudioPlayer,
    PlaybackBackend,
    materialize_audio,
)

__all__ = [
    "DEFAULT_PLAYBACK_CHANNELS",
    "DEFAULT_PLAYBACK_SAMPLE_RATE",
    "MaterializedAudio",
    "MaterializedAudioPlayer",
    "PlaybackBackend",
    "materialize_audio",
]
