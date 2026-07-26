"""Shared contracts and adapters with compatibility-preserving lazy exports.

Most repository code should import from the owning module. The flat exports are
kept for existing callers, but resolving one name imports only that name's module
instead of eagerly loading every SDK, parser, and optional audio dependency.
"""

from __future__ import annotations

from importlib import import_module
from typing import Final


_EXPORT_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "anilist_search": (
        "ANILIST_SEARCH_BASE_URL_ENV",
        "ANILIST_SEARCH_GATEWAY_PATH",
        "AnimeMetadata",
        "AniListSearchClient",
        "HttpAniListSearchClient",
        "SearchResult",
        "SearchResponse",
    ),
    "anime_audio": (
        "ANIME_AUDIO_BASE_URL_ENV",
        "ANIME_AUDIO_GATEWAY_PATH",
        "AnimeAudioArtifact",
        "AnimeAudioClient",
        "AnimeAudioEpisode",
        "AnimeAudioInventory",
        "AnimeAudioInventorySeries",
        "AnimeAudioNotFoundError",
        "AnimeAudioSeries",
        "AnimeAudioSubtitle",
        "AnimeAudioSubtitleContent",
        "HttpAnimeAudioClient",
    ),
    "asr": (
        "AsrBackend",
        "AsyncAsrBackend",
        "AsrJob",
        "AsrJobRecord",
        "AsrJobStatus",
        "AsrJobSubmitter",
        "AsrRequest",
        "AsrRuntimeOptions",
        "AsrSegment",
        "AsrTask",
        "AsrTranscript",
        "asr_request_from_chunks",
    ),
    "audio": (
        "InMemoryAudioChunk",
        "full_audio_chunk",
        "materialize_audio_chunk",
        "probe_audio_source",
        "resolve_audio_source",
        "write_audio_chunk",
    ),
    "audio_contracts": ("AudioChunk", "AudioFormat", "AudioSource"),
    "audio_library": (
        "PORTABLE_AAC_V1",
        "AnimeAudioManifest",
        "AnimeAudioSeriesMetadata",
        "ArtifactRecord",
        "AudioProfile",
        "AudioStreamProbe",
        "CoverArtifact",
        "EpisodeMapping",
        "ManifestEpisode",
        "MaterializationPlan",
        "SourceMediaProbe",
        "SubtitleArtifactRecord",
        "SubtitleStreamProbe",
    ),
    "audio_manifest": (
        "MANIFEST_KIND",
        "SCHEMA_VERSION",
        "manifest_from_mapping",
        "manifest_to_mapping",
    ),
    "config": (
        "APP_CONFIG_DIR_NAME",
        "CONFIG_ENV_VAR",
        "CONFIG_FILE_NAME",
        "AsrBackendConfig",
        "AsrConfig",
        "BackendConfig",
        "BackendFactoryRegistry",
        "BackendGroupConfig",
        "ForcedAlignmentBackendConfig",
        "ForcedAlignmentConfig",
        "JaMediaConfig",
        "JaMediaSettings",
        "SubtitleConfig",
        "default_config_path",
        "load_config",
        "resolve_config_path",
        "xdg_config_home",
    ),
    "kitsunekko": (
        "HttpKitsunekkoSubtitlesClient",
        "KitsunekkoFileListResponse",
        "KitsunekkoStats",
        "KitsunekkoSubtitlesClient",
        "anilist_content_path",
        "anilist_episode_content_path",
        "anilist_episode_files_path",
        "anilist_files_path",
        "file_content_path",
        "file_metadata_path",
        "tvdb_content_path",
        "tvdb_episode_content_path",
        "tvdb_episode_files_path",
        "tvdb_files_path",
    ),
    "media_filename": (
        "ParsedMediaFilename",
        "first_positive_episode",
        "parse_media_filename",
        "suggest_ordinary_episode",
    ),
    "reader": (
        "ReaderSession",
        "TimelineSpan",
        "TimelineTrack",
        "reader_session_from_cues",
    ),
    "subtitle_lid": (
        "SampledLanguageMetrics",
        "SubtitleLanguage",
        "SubtitleLanguageAnalysis",
        "SubtitleLanguageIdConfig",
        "SubtitleScriptMetrics",
        "analyze_srt_language",
        "analyze_subtitle_language",
        "calculate_script_metrics",
        "evenly_spaced_sample",
        "fasttext_line_detector",
        "sample_line_languages",
        "subtitle_text_lines",
    ),
    "subsync": (
        "SubtitleCandidate",
        "infer_episode_number",
        "is_supported_subtitle_file",
    ),
    "transcripts": (
        "SubtitleCue",
        "clean_ass_text",
        "format_srt",
        "format_srt_timestamp",
        "parse_ass",
        "parse_ass_timestamp",
        "parse_srt",
        "parse_srt_timestamp",
        "read_ass",
        "read_srt",
        "read_subtitle",
        "shift_srt_cues",
    ),
    "vad": (
        "SpeechSpan",
        "VadBackend",
        "VadOptions",
        "VadTimeline",
        "normalize_speech_spans",
        "plan_vad_splits",
        "speech_chunks_from_timeline",
        "speech_chunks_from_timelines",
        "validate_speech_spans",
    ),
}

_EXPORTS: Final[dict[str, str]] = {
    name: module_name
    for module_name, names in _EXPORT_GROUPS.items()
    for name in names
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    """Resolve a legacy flat export without importing unrelated modules."""

    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include lazy exports in interactive discovery."""

    return sorted({*globals(), *_EXPORTS})
