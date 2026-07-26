"""Lazy audio loading through the same path used by the subsync TUI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath

import duckdb

from ja_media_frontend.audio import MaterializedAudio, materialize_audio
from ja_media_frontend.subsync.audio_source import (
    DEFAULT_AUDIO_PROFILE,
    resolve_subsync_audio,
)
from ja_media_core.bronze import parse_bronze_manifest

from subtitle_alignment.access import DevReadAccess


@dataclass(frozen=True)
class LoadedReviewAudio:
    """Decoded episode audio plus the resolver's provenance message."""

    audio: MaterializedAudio
    status: str


def load_review_audio(
    result: Path,
    anilist_id: int,
    episode: int,
    *,
    profile: str = DEFAULT_AUDIO_PROFILE,
) -> LoadedReviewAudio:
    """Fetch the indexed artifact lazily, then use subsync's PCM materializer."""

    try:
        selected = resolve_subsync_audio(
            None,
            anilist_id=anilist_id,
            episode_number=episode,
            profile=profile,
        )
    except ValueError:
        source = _bronze_audio(result, anilist_id, episode)
        return LoadedReviewAudio(
            audio=materialize_audio(source),
            status=f"using cached bronze audio ({source.name})",
        )
    return LoadedReviewAudio(
        audio=materialize_audio(selected.playback_path),
        status=selected.status,
    )


def _bronze_audio(result: Path, anilist_id: int, episode: int) -> Path:
    summary = json.loads((result / "summary.json").read_text())
    dataset = Path(__file__).resolve().parents[2] / ".cache" / summary["dataset_id"]
    connection = duckdb.connect(str(dataset / "evaluation.duckdb"), read_only=True)
    try:
        row = connection.execute(
            """SELECT audio_capture_id, manifest_key, manifest_etag,
                      input_fingerprint
                 FROM episodes
                WHERE cast(anilist_id AS BIGINT) = ?
                  AND cast(episode AS INTEGER) = ?
                LIMIT 1""",
            [anilist_id, episode],
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"Phase 0 has no audio locator for {anilist_id}:{episode}")
    capture_id, manifest_key, manifest_etag, fingerprint = row
    access = DevReadAccess.from_repository_config()
    payload = access.bronze.read_manifest(
        manifest_key, expected_etag=manifest_etag
    )
    manifest = parse_bronze_manifest(
        payload, capture_id=capture_id, manifest_key=manifest_key
    )
    suffix = PurePosixPath(manifest.audio.object_name).suffix or ".audio"
    target = dataset / "objects" / "audio" / f"{fingerprint}{suffix}"
    if target.is_file():
        return target
    access.bronze.download_file(
        _audio_object_key(manifest_key, manifest.audio.object_name), target
    )
    return target


def _audio_object_key(manifest_key: str, filename: str) -> str:
    path = PurePosixPath(manifest_key)
    if path.parent.name != "metadata":
        raise ValueError(f"unexpected bronze manifest layout: {manifest_key}")
    return str(path.parent.parent / PurePosixPath(filename).name)
