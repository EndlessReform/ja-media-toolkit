"""Interactive planning and resumable execution for Phase 1 ingest."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from ja_media_core.anilist_search import AniListSearchClient, SearchResult
from ja_media_core.audio_library import (
    AnimeAudioSeriesMetadata,
    AudioStreamProbe,
    EpisodeMapping,
    MaterializationPlan,
    PORTABLE_AAC_V1,
    SourceMediaProbe,
)
from ja_media_core.media_filename import parse_media_filename
from ja_media_frontend.audio_library.discovery import (
    choose_unambiguous_audio_stream,
    discover_media,
    identity_search_query,
    probe_media,
    text_subtitle_streams,
)
from ja_media_frontend.audio_library.executor import IngestSummary, execute_ingest_plan
from ja_media_frontend.audio_library.mapping import resolve_episode_keys
from ja_media_frontend.audio_library.metadata import (
    SELECTED_FIELDS,
    normalize_anilist_metadata,
)


class WizardPrompts(Protocol):
    """User decisions required before the workflow is allowed to write."""

    def choose_anime(
        self, query: str, candidates: Sequence[SearchResult]
    ) -> int | str | None: ...

    def confirm_series(self, metadata: AnimeAudioSeriesMetadata) -> bool: ...

    def map_episode(
        self,
        source: SourceMediaProbe,
        suggested_key: str | None,
        *,
        position: int,
        total: int,
    ) -> str | None: ...

    def choose_audio_stream(self, source: SourceMediaProbe) -> AudioStreamProbe | None: ...

    def confirm_plan(self, plan: MaterializationPlan) -> bool: ...

    def notice(self, message: str) -> None: ...


@dataclass(frozen=True)
class IngestWizardRequest:
    """Inputs that constrain interactive plan construction."""

    source: Path
    destination: Path
    client: AniListSearchClient
    prompts: WizardPrompts
    anilist_id: int | None = None
    audio_stream_ordinal: int | None = None
    preferred_languages: tuple[str, ...] = ("jpn", "ja")
    extract_subtitles: bool = True


def build_ingest_plan(request: IngestWizardRequest) -> MaterializationPlan | None:
    """Resolve every identity and mapping decision before writing anything."""

    _validate_environment(request.source, request.destination)
    anilist_id = request.anilist_id or _search_for_identity(request)
    if anilist_id is None:
        return None
    raw_metadata = request.client.anime(anilist_id, fields=SELECTED_FIELDS)
    if raw_metadata.anilist_id != anilist_id:
        raise ValueError(
            f"AniList detail response returned {raw_metadata.anilist_id}, expected {anilist_id}"
        )
    series = normalize_anilist_metadata(raw_metadata)
    if not request.prompts.confirm_series(series):
        return None

    paths = discover_media(request.source)
    if not paths:
        raise ValueError(f"no supported media files found in {request.source}")
    probes = tuple(probe_media(path) for path in paths)
    mappings: list[EpisodeMapping] = []
    for source, key in resolve_episode_keys(probes, request.prompts):
        stream = _resolve_stream(source, request)
        if stream is None:
            request.prompts.notice(f"Excluded {source.path.name}: no audio stream selected.")
            continue
        subtitle_streams = (
            text_subtitle_streams(source) if request.extract_subtitles else ()
        )
        mappings.append(
            EpisodeMapping(
                episode_key=key,
                source=source,
                stream=stream,
                subtitle_streams=subtitle_streams,
            )
        )
    if not mappings:
        raise ValueError("no episodes were approved for ingest")

    plan = MaterializationPlan(
        source_root=request.source,
        destination_root=request.destination,
        series=series,
        mappings=tuple(sorted(mappings, key=lambda item: int(item.episode_key))),
        profile=PORTABLE_AAC_V1,
    )
    return plan if request.prompts.confirm_plan(plan) else None


def _search_for_identity(request: IngestWizardRequest) -> int | None:
    query = identity_search_query(request.source)
    while True:
        response = request.client.search(query, top_k=10, all_formats=True)
        selected = request.prompts.choose_anime(query, response.results)
        if isinstance(selected, int):
            return selected
        if isinstance(selected, str):
            query = selected
            continue
        return None


def _resolve_stream(
    source: SourceMediaProbe, request: IngestWizardRequest
) -> AudioStreamProbe | None:
    if request.audio_stream_ordinal is not None:
        for stream in source.audio_streams:
            if stream.audio_ordinal == request.audio_stream_ordinal:
                return stream
        raise ValueError(
            f"{source.path.name} has no audio stream ordinal "
            f"{request.audio_stream_ordinal}"
        )
    selected = choose_unambiguous_audio_stream(
        source, preferred_languages=request.preferred_languages
    )
    return selected or request.prompts.choose_audio_stream(source)


def _validate_environment(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise ValueError(f"source directory does not exist: {source}")
    if not destination.is_dir():
        raise ValueError(f"destination directory does not exist: {destination}")
    for executable in ("ffmpeg", "ffprobe"):
        if shutil.which(executable) is None:
            raise ValueError(f"required executable is not available: {executable}")
