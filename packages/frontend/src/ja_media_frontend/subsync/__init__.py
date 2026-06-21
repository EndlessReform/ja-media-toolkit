"""Shared subtitle review frontends and application services.

The package deliberately separates subtitle-domain primitives, which live in
``ja_media_core``, from frontend application services such as path discovery,
Kitsunekko retrieval, temporary-file materialization, and sidecar promotion.
Textual and browser code should depend on these services instead of growing
their own filesystem and network implementations.
"""

from ja_media_frontend.subsync.service import (
    SubtitleDestinationExistsError,
    SubtitleLookup,
    SubtitleTrack,
    build_subtitle_track,
    fetch_episode_files,
    fetch_series_files,
    load_subtitle_track,
    materialize_remote_track,
    promote_subtitle,
)
from ja_media_frontend.subsync.utils import (
    discover_subtitle_file,
    is_supported_remote_subtitle,
    resolve_subtitle_inputs,
    search_remote_subtitles,
    sidecar_path,
)

__all__ = [
    "SubtitleDestinationExistsError",
    "SubtitleLookup",
    "SubtitleTrack",
    "build_subtitle_track",
    "discover_subtitle_file",
    "fetch_episode_files",
    "fetch_series_files",
    "is_supported_remote_subtitle",
    "load_subtitle_track",
    "materialize_remote_track",
    "promote_subtitle",
    "resolve_subtitle_inputs",
    "search_remote_subtitles",
    "sidecar_path",
]
