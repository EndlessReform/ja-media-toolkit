"""Hidden embedded-subtitle extraction for audio-library ingest."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ja_media_core.audio_library import EpisodeMapping, SubtitleArtifactRecord
from ja_media_frontend.audio_library.materialize import (
    materialize_subtitle,
    subtitle_artifact_record,
    subtitle_filename,
    verify_subtitle_artifact,
)

SUBTITLE_DIR_NAME = "_subs"


def materialize_episode_subtitles(
    mapping: EpisodeMapping,
    series_dir: Path,
    *,
    resume: bool,
    replace_existing: bool,
    notice: Callable[[str], None],
) -> tuple[SubtitleArtifactRecord, ...]:
    """Extract every selected text subtitle stream into the hidden subtitle dir."""

    if not mapping.subtitle_streams:
        return ()

    subtitle_dir = series_dir / SUBTITLE_DIR_NAME
    subtitle_dir.mkdir(parents=True, exist_ok=True)
    records: list[SubtitleArtifactRecord] = []
    for stream in mapping.subtitle_streams:
        filename = subtitle_filename(mapping.episode_key, stream)
        relative_path = f"{SUBTITLE_DIR_NAME}/{filename}"
        destination = subtitle_dir / filename
        try:
            if not destination.exists() or replace_existing:
                records.append(
                    materialize_subtitle(
                        mapping,
                        stream,
                        destination,
                        relative_path=relative_path,
                    )
                )
            else:
                verify_subtitle_artifact(destination)
                records.append(
                    subtitle_artifact_record(
                        stream,
                        destination,
                        relative_path=relative_path,
                    )
                )
        except Exception as error:
            notice(
                "Subtitle extraction failed for "
                f"{mapping.source_path.name} {stream.global_index}: {error}"
            )
    return tuple(records)
