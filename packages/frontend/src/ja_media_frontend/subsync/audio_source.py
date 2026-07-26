"""Compatibility exports for shared derived-audio cache resolution."""

from ja_media_media.anime_audio_cache import (
    DEFAULT_AUDIO_PROFILE,
    SubsyncAudioSelection,
    default_audio_cache_dir,
    resolve_subsync_audio,
)

__all__ = [
    "DEFAULT_AUDIO_PROFILE",
    "SubsyncAudioSelection",
    "default_audio_cache_dir",
    "resolve_subsync_audio",
]
