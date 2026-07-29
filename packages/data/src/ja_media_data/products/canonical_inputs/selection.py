"""Select accepted episode bindings and enumerate deterministic inputs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath

from ja_media_core.bronze import BronzeStream, parse_bronze_manifest

from ja_media_data.storage.bronze import BronzeStore
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.products.canonical_inputs.models import (
    CanonicalEpisodeInput,
    CanonicalSubtitleInput,
)


@dataclass(frozen=True)
class Candidate:
    """One automatically accepted or explicitly overridden capture candidate."""

    namespace: str
    series_id: str
    episode: str
    capture_id: str
    binding_id: str
    binding_source: str
    bucket: str
    manifest_key: str
    manifest_etag: str
    manifest_modified_at: datetime
    audio_object_bucket: str
    audio_object_key: str
    audio_stream_index: int
    audio_codec: str | None
    audio_declared_language: str | None

    @property
    def locator(self) -> tuple[str, str, str]:
        return self.namespace, self.series_id, self.episode


def select_canonical_candidates(
    repository: DuckLakeRepository,
) -> list[Candidate]:
    """Apply overrides, then choose the latest capture for every locator."""

    grouped: dict[tuple[str, str, str], list[Candidate]] = defaultdict(list)
    for candidate in _effective_candidates(repository):
        grouped[candidate.locator].append(candidate)
    selected = [
        max(
            items,
            key=lambda item: (
                item.manifest_modified_at,
                item.manifest_key,
                item.capture_id,
            ),
        )
        for items in grouped.values()
    ]
    return sorted(selected, key=lambda item: item.locator)


def build_canonical_rows(
    selected: Sequence[Candidate],
    store: BronzeStore,
    *,
    policy_version: str,
    fingerprint: Callable[..., str],
    stable_id: Callable[..., str],
) -> tuple[list[CanonicalEpisodeInput], list[CanonicalSubtitleInput]]:
    """Read selected manifests and pin their exact audio/subtitle objects."""

    episodes: list[CanonicalEpisodeInput] = []
    subtitles: list[CanonicalSubtitleInput] = []
    for candidate in selected:
        payload = store.read_manifest(
            candidate.manifest_key, expected_etag=candidate.manifest_etag
        )
        manifest = parse_bronze_manifest(
            payload,
            capture_id=candidate.capture_id,
            manifest_key=candidate.manifest_key,
        )
        canonical_fp = fingerprint(
            policy_version,
            asdict(candidate),
        )
        canonical_id = stable_id("canonical", *candidate.locator, canonical_fp)
        episodes.append(
            CanonicalEpisodeInput(
                canonical_id=canonical_id,
                namespace=candidate.namespace,
                series_id=candidate.series_id,
                episode=candidate.episode,
                audio_capture_id=candidate.capture_id,
                binding_id=candidate.binding_id,
                binding_source=candidate.binding_source,
                manifest_bucket=candidate.bucket,
                manifest_key=candidate.manifest_key,
                manifest_etag=candidate.manifest_etag,
                manifest_modified_at=candidate.manifest_modified_at,
                audio_object_bucket=candidate.audio_object_bucket,
                audio_object_key=candidate.audio_object_key,
                audio_stream_index=candidate.audio_stream_index,
                audio_codec=candidate.audio_codec,
                audio_declared_language=candidate.audio_declared_language,
                input_fingerprint=canonical_fp,
            )
        )
        for subtitle in manifest.subtitles:
            subtitles.append(
                _subtitle_row(
                    candidate,
                    canonical_id,
                    canonical_fp,
                    subtitle,
                    capture_stem=manifest.stem,
                    fingerprint=fingerprint,
                    stable_id=stable_id,
                )
            )
    return episodes, subtitles


def _effective_candidates(repository: DuckLakeRepository) -> list[Candidate]:
    rows = repository.connection.execute(
        """SELECT accepted.namespace, accepted.series_id, accepted.episode,
                  accepted.audio_capture_id, accepted.acceptance_id,
                  capture.manifest_bucket, capture.manifest_key,
                  capture.manifest_etag,
                  coalesce(capture.manifest_modified_at, capture.last_observed_at),
                  audio.selected_audio_object_bucket,
                  audio.selected_audio_object_key,
                  audio.selected_audio_stream_index,
                  audio.selected_audio_codec,
                  audio.selected_audio_declared_language
           FROM accepted_bindings_auto AS accepted
           JOIN bronze_captures AS capture
             ON capture.capture_id = accepted.audio_capture_id
           JOIN capture_audio_eligibility AS audio
             ON audio.capture_id = accepted.audio_capture_id
            AND audio.manifest_bucket = capture.manifest_bucket
            AND audio.manifest_key = capture.manifest_key
            AND audio.manifest_etag = capture.manifest_etag
            AND audio.status = 'eligible'"""
    ).fetchall()
    candidates = [Candidate(*row[:5], "automatic", *row[5:]) for row in rows]
    if repository.override_repository is None:
        return candidates
    overrides = tuple(repository.override_repository.iter_current_overrides())
    masked = {(item.namespace, item.series_id, item.episode) for item in overrides}
    candidates = [item for item in candidates if item.locator not in masked]
    for override in overrides:
        if override.audio_capture_id is None:
            continue
        capture = repository.connection.execute(
            """SELECT capture.manifest_bucket, capture.manifest_key,
                      capture.manifest_etag,
                      coalesce(capture.manifest_modified_at,
                               capture.last_observed_at),
                      audio.selected_audio_object_bucket,
                      audio.selected_audio_object_key,
                      audio.selected_audio_stream_index,
                      audio.selected_audio_codec,
                      audio.selected_audio_declared_language
               FROM bronze_captures AS capture
               JOIN capture_audio_eligibility AS audio
                 ON audio.capture_id = capture.capture_id
                AND audio.manifest_bucket = capture.manifest_bucket
                AND audio.manifest_key = capture.manifest_key
                AND audio.manifest_etag = capture.manifest_etag
                AND audio.status = 'eligible'
               WHERE capture.capture_id = ? LIMIT 1""",
            [override.audio_capture_id],
        ).fetchone()
        if capture is not None:
            candidates.append(
                Candidate(
                    override.namespace,
                    override.series_id,
                    override.episode,
                    override.audio_capture_id,
                    override.override_id,
                    "override",
                    *capture,
                )
            )
    return candidates


def _subtitle_row(
    candidate: Candidate,
    canonical_id: str,
    canonical_fp: str,
    stream: BronzeStream,
    *,
    capture_stem: str,
    fingerprint: Callable[..., str],
    stable_id: Callable[..., str],
) -> CanonicalSubtitleInput:
    object_key = subtitle_object_key(
        candidate.manifest_key, stream.object_name, capture_stem=capture_stem
    )
    input_fp = fingerprint(canonical_fp, object_key, stream.stream_index)
    return CanonicalSubtitleInput(
        stable_id("subtitle", canonical_id, str(stream.stream_index), object_key),
        canonical_id,
        *candidate.locator,
        candidate.capture_id,
        candidate.bucket,
        object_key,
        stream.stream_index,
        stream.codec,
        stream.declared_language,
        input_fp,
    )


def audio_object_key(manifest_key: str, name: str) -> str:
    """Resolve an audio name beneath the configured manifest's series root."""

    return _object_key(manifest_key, name, default_directory=None)


def subtitle_object_key(
    manifest_key: str, name: str, *, capture_stem: str
) -> str:
    """Resolve v2 explicit keys and the legacy series/subs/stem layout."""

    return _object_key(
        manifest_key, name, default_directory=PurePosixPath("subs") / capture_stem
    )


def _object_key(
    manifest_key: str, name: str, *, default_directory: PurePosixPath | None
) -> str:
    manifest = PurePosixPath(manifest_key)
    if manifest.parent.name != "metadata":
        raise ValueError(f"unexpected bronze manifest layout: {manifest_key}")
    series_root = manifest.parent.parent
    candidate = PurePosixPath(name)
    if str(candidate).startswith(str(series_root) + "/"):
        return str(candidate)
    if len(candidate.parts) > 1:
        return str(series_root / candidate)
    parent = (
        series_root if default_directory is None else series_root / default_directory
    )
    return str(parent / candidate.name)
