"""Interactive planning and resumable execution for Phase 1 ingest."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Protocol, Sequence

from ja_media_core.anilist_search import AniListSearchClient, SearchResult
from ja_media_core.audio_library import (
    AnimeAudioManifest,
    AnimeAudioSeriesMetadata,
    AudioStreamProbe,
    EpisodeMapping,
    ManifestEpisode,
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
)
from ja_media_frontend.audio_library.manifest import (
    load_manifest,
    write_manifest_atomic,
    write_metadata_atomic,
)
from ja_media_frontend.audio_library.mapping import resolve_episode_keys
from ja_media_frontend.audio_library.materialize import (
    artifact_filename,
    materialize_episode,
    verify_audio_artifact,
)
from ja_media_frontend.audio_library.metadata import (
    SELECTED_FIELDS,
    download_cover,
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


@dataclass(frozen=True)
class IngestSummary:
    """Materialization outcome suitable for concise CLI reporting."""

    created: tuple[str, ...]
    skipped: tuple[str, ...]
    failed: tuple[str, ...]


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
        mappings.append(EpisodeMapping(episode_key=key, source=source, stream=stream))
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


def execute_ingest_plan(
    plan: MaterializationPlan,
    *,
    resume: bool = False,
    replace_existing: bool = False,
    notice: Callable[[str], None] = print,
) -> IngestSummary:
    """Materialize a confirmed plan with per-episode manifest checkpoints."""

    series_dir = plan.series_directory
    series_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = series_dir / ".ja-media.json"
    metadata_path = series_dir / "metadata.json"
    manifest = _initial_manifest(plan, manifest_path)
    if manifest.series.anilist_id != plan.series.anilist_id:
        raise ValueError("existing manifest belongs to a different AniList series")

    if plan.series.cover_url and manifest.cover is None:
        cover_path = series_dir / "cover.jpg"
        if not cover_path.exists() or replace_existing:
            try:
                cover = download_cover(plan.series.cover_url, cover_path)
                manifest = replace(manifest, cover=cover)
            except Exception as error:
                notice(f"Cover download failed; continuing without it: {error}")
        else:
            notice("Existing cover.jpg is not in the manifest; leaving it untouched.")
    write_manifest_atomic(manifest_path, manifest)

    created: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for mapping in plan.mappings:
        filename = artifact_filename(mapping.episode_key)
        destination = series_dir / filename
        existing = _episode_by_key(manifest, mapping.episode_key)
        try:
            if _can_resume(existing, mapping, plan, destination, resume):
                verify_audio_artifact(destination, plan.profile)
                skipped.append(filename)
                continue
            if destination.exists() and not replace_existing:
                raise FileExistsError(
                    f"{filename} already exists but is not a matching resumable artifact; "
                    "use --replace after reviewing it"
                )
            artifact = materialize_episode(
                mapping, destination, plan.series, plan.profile
            )
            episode = ManifestEpisode(
                episode_key=mapping.episode_key,
                source_relative_path=str(
                    mapping.source_path.relative_to(plan.source_root)
                ),
                source_size_bytes=mapping.source.size_bytes,
                source_mtime_ns=mapping.source.mtime_ns,
                global_stream_index=mapping.stream.global_index,
                audio_stream_ordinal=mapping.stream.audio_ordinal,
                audio_codec=mapping.stream.codec,
                audio_language=mapping.stream.language,
                artifact=artifact,
                created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
            manifest = _with_episode(manifest, episode)
            write_manifest_atomic(manifest_path, manifest)
            created.append(filename)
        except Exception as error:
            failed.append(f"{filename}: {error}")
            notice(f"Failed {filename}: {error}")
    write_metadata_atomic(metadata_path, manifest)
    return IngestSummary(tuple(created), tuple(skipped), tuple(failed))


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


def _initial_manifest(plan: MaterializationPlan, path: Path) -> AnimeAudioManifest:
    if not path.exists():
        return AnimeAudioManifest(series=plan.series, profile=plan.profile)
    manifest = load_manifest(path)
    if manifest.profile != plan.profile:
        raise ValueError(
            "existing manifest uses a different profile; Phase 1 will not "
            "silently treat it as portable-aac-v1"
        )
    return replace(manifest, series=plan.series)


def _episode_by_key(
    manifest: AnimeAudioManifest, episode_key: str
) -> ManifestEpisode | None:
    return next(
        (item for item in manifest.episodes if item.episode_key == episode_key),
        None,
    )


def _can_resume(
    existing: ManifestEpisode | None,
    mapping: EpisodeMapping,
    plan: MaterializationPlan,
    destination: Path,
    resume: bool,
) -> bool:
    if not resume or existing is None or not destination.is_file():
        return False
    relative = str(mapping.source_path.relative_to(plan.source_root))
    return (
        existing.source_relative_path == relative
        and existing.source_size_bytes == mapping.source.size_bytes
        and existing.source_mtime_ns == mapping.source.mtime_ns
        and existing.global_stream_index == mapping.stream.global_index
        and existing.artifact.relative_path == destination.name
    )


def _with_episode(
    manifest: AnimeAudioManifest, episode: ManifestEpisode
) -> AnimeAudioManifest:
    episodes = [item for item in manifest.episodes if item.episode_key != episode.episode_key]
    episodes.append(episode)
    episodes.sort(key=lambda item: int(item.episode_key))
    return replace(manifest, episodes=tuple(episodes))
