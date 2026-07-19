"""Select accepted episode bindings and enumerate deterministic inputs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath

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
    fingerprint: Callable[..., str],
    stable_id: Callable[..., str],
) -> tuple[list[CanonicalEpisodeInput], list[CanonicalSubtitleInput]]:
    """Read selected manifests and enumerate their exact subtitle objects."""

    episodes: list[CanonicalEpisodeInput] = []
    subtitles: list[CanonicalSubtitleInput] = []
    for candidate in selected:
        canonical_fp = fingerprint(asdict(candidate))
        canonical_id = stable_id("canonical", *candidate.locator, canonical_fp)
        episodes.append(
            CanonicalEpisodeInput(
                canonical_id,
                *candidate.locator,
                candidate.capture_id,
                candidate.binding_id,
                candidate.binding_source,
                candidate.bucket,
                candidate.manifest_key,
                candidate.manifest_etag,
                candidate.manifest_modified_at,
                canonical_fp,
            )
        )
        manifest = store.read_manifest(
            candidate.manifest_key, expected_etag=candidate.manifest_etag
        )
        raw_subtitles = manifest.get("subtitles", [])
        if not isinstance(raw_subtitles, list):
            raise ValueError(
                f"manifest subtitles are not a list: {candidate.manifest_key}"
            )
        for raw in raw_subtitles:
            subtitles.append(
                _subtitle_row(
                    candidate,
                    canonical_id,
                    canonical_fp,
                    raw,
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
                  coalesce(capture.manifest_modified_at, capture.last_observed_at)
           FROM accepted_bindings_auto AS accepted
           JOIN bronze_captures AS capture
             ON capture.capture_id = accepted.audio_capture_id"""
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
            """SELECT manifest_bucket, manifest_key, manifest_etag,
                      coalesce(manifest_modified_at, last_observed_at)
               FROM bronze_captures WHERE capture_id = ? LIMIT 1""",
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
    raw: object,
    *,
    fingerprint: Callable[..., str],
    stable_id: Callable[..., str],
) -> CanonicalSubtitleInput:
    if not isinstance(raw, Mapping):
        raise ValueError(f"subtitle stream is not an object: {candidate.manifest_key}")
    stream_index = raw.get("stream_index")
    if isinstance(stream_index, bool) or not isinstance(stream_index, int):
        raise ValueError("subtitle stream_index must be an integer")
    name = raw.get("key") or raw.get("filename")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("subtitle stream has no key or filename")
    object_key = subtitle_object_key(candidate.manifest_key, name.strip())
    input_fp = fingerprint(canonical_fp, object_key, stream_index)
    return CanonicalSubtitleInput(
        stable_id("subtitle", canonical_id, str(stream_index), object_key),
        canonical_id,
        *candidate.locator,
        candidate.capture_id,
        candidate.bucket,
        object_key,
        stream_index,
        _text(raw.get("source_codec") or raw.get("codec")),
        _text(raw.get("declared_language")),
        input_fp,
    )


def subtitle_object_key(manifest_key: str, name: str) -> str:
    """Resolve v2 explicit keys and the legacy series/subs/stem layout."""

    manifest = PurePosixPath(manifest_key)
    series_root = manifest.parent.parent
    candidate = PurePosixPath(name)
    if str(candidate).startswith(str(series_root) + "/"):
        return str(candidate)
    if len(candidate.parts) > 1:
        return str(series_root / candidate)
    return str(series_root / "subs" / manifest.stem / candidate.name)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
