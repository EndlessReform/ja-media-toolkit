"""Delta-aware execution for confirmed audio-library ingest plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from ja_media_core.audio_library import (
    AnimeAudioManifest,
    ArtifactRecord,
    EpisodeMapping,
    ManifestEpisode,
    MaterializationPlan,
    SubtitleArtifactRecord,
)
from ja_media_frontend.audio_library.manifest import (
    load_manifest,
    write_manifest_atomic,
    write_metadata_atomic,
)
from ja_media_frontend.audio_library.materialize import (
    artifact_filename,
    materialize_episode,
    verify_audio_artifact,
)
from ja_media_frontend.audio_library.metadata import download_cover
from ja_media_frontend.audio_library.subtitles import materialize_episode_subtitles


@dataclass(frozen=True)
class IngestSummary:
    """Materialization outcome suitable for concise CLI reporting."""

    created: tuple[str, ...]
    skipped: tuple[str, ...]
    failed: tuple[str, ...]


def execute_ingest_plan(
    plan: MaterializationPlan,
    *,
    resume: bool = False,
    replace_existing: bool = False,
    notice: Callable[[str], None] = print,
) -> IngestSummary:
    """Materialize a confirmed plan with per-episode manifest checkpoints.

    Execution is intentionally delta-aware: verified audio already present on
    disk is adopted or skipped while missing subtitles are extracted into the
    hidden sidecar directory.  ``--replace`` remains the opt-in path for
    overwriting audio or subtitle artifacts.
    """

    series_dir = plan.series_directory
    series_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = series_dir / ".ja-media.json"
    metadata_path = series_dir / "metadata.json"
    manifest = _initial_manifest(plan, manifest_path)
    if manifest.series.anilist_id != plan.series.anilist_id:
        raise ValueError("existing manifest belongs to a different AniList series")

    manifest = _ensure_cover(manifest, plan, series_dir, replace_existing, notice)
    write_manifest_atomic(manifest_path, manifest)

    created: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for mapping in plan.mappings:
        filename = artifact_filename(mapping.episode_key)
        destination = series_dir / filename
        existing = _episode_by_key(manifest, mapping.episode_key)
        try:
            episode, created_audio = _materialize_or_adopt_episode(
                mapping,
                plan,
                series_dir,
                destination,
                existing,
                replace_existing=replace_existing,
                notice=notice,
            )
            manifest = _with_episode(manifest, episode)
            write_manifest_atomic(manifest_path, manifest)
            (created if created_audio else skipped).append(filename)
        except Exception as error:
            failed.append(f"{filename}: {error}")
            notice(f"Failed {filename}: {error}")
    write_metadata_atomic(metadata_path, manifest)
    return IngestSummary(tuple(created), tuple(skipped), tuple(failed))


def _materialize_or_adopt_episode(
    mapping: EpisodeMapping,
    plan: MaterializationPlan,
    series_dir: Path,
    destination: Path,
    existing: ManifestEpisode | None,
    *,
    replace_existing: bool,
    notice: Callable[[str], None],
) -> tuple[ManifestEpisode, bool]:
    if existing and _matches_existing_episode(existing, mapping, plan, destination):
        artifact = verify_audio_artifact(destination, plan.profile)
        subtitles = _subtitles_for(mapping, series_dir, replace_existing, notice)
        return replace(
            existing,
            artifact=artifact,
            subtitles=subtitles or existing.subtitles,
        ), False

    if destination.exists() and not replace_existing:
        if existing is not None:
            raise FileExistsError(
                f"{destination.name} exists but its manifest entry no longer "
                "matches the selected source; use --replace after reviewing it"
            )
        artifact = verify_audio_artifact(destination, plan.profile)
        subtitles = _subtitles_for(mapping, series_dir, replace_existing, notice)
        return _episode_record(mapping, plan, artifact, subtitles), False

    artifact = materialize_episode(mapping, destination, plan.series, plan.profile)
    subtitles = _subtitles_for(mapping, series_dir, replace_existing, notice)
    return _episode_record(mapping, plan, artifact, subtitles), True


def _subtitles_for(
    mapping: EpisodeMapping,
    series_dir: Path,
    replace_existing: bool,
    notice: Callable[[str], None],
) -> tuple[SubtitleArtifactRecord, ...]:
    return materialize_episode_subtitles(
        mapping,
        series_dir,
        resume=True,
        replace_existing=replace_existing,
        notice=notice,
    )


def _ensure_cover(
    manifest: AnimeAudioManifest,
    plan: MaterializationPlan,
    series_dir: Path,
    replace_existing: bool,
    notice: Callable[[str], None],
) -> AnimeAudioManifest:
    if not plan.series.cover_url or manifest.cover is not None:
        return manifest
    cover_path = series_dir / "cover.jpg"
    if not cover_path.exists() or replace_existing:
        try:
            return replace(
                manifest,
                cover=download_cover(plan.series.cover_url, cover_path),
            )
        except Exception as error:
            notice(f"Cover download failed; continuing without it: {error}")
            return manifest
    notice("Existing cover.jpg is not in the manifest; leaving it untouched.")
    return manifest


def _episode_record(
    mapping: EpisodeMapping,
    plan: MaterializationPlan,
    artifact: ArtifactRecord,
    subtitles: tuple[SubtitleArtifactRecord, ...],
) -> ManifestEpisode:
    return ManifestEpisode(
        episode_key=mapping.episode_key,
        source_relative_path=str(mapping.source_path.relative_to(plan.source_root)),
        source_size_bytes=mapping.source.size_bytes,
        source_mtime_ns=mapping.source.mtime_ns,
        global_stream_index=mapping.stream.global_index,
        audio_stream_ordinal=mapping.stream.audio_ordinal,
        audio_codec=mapping.stream.codec,
        audio_language=mapping.stream.language,
        artifact=artifact,
        created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        subtitles=subtitles,
    )


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


def _matches_existing_episode(
    existing: ManifestEpisode,
    mapping: EpisodeMapping,
    plan: MaterializationPlan,
    destination: Path,
) -> bool:
    if not destination.is_file():
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
