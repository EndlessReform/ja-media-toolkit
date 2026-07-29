"""Deterministic compiler for canonical episode and subtitle inputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.products.canonical_inputs.models import (
    CanonicalEpisodeInput,
    CanonicalSubtitleInput,
)
from ja_media_data.products.canonical_inputs.selection import (
    build_canonical_rows,
    select_canonical_candidates,
)
from ja_media_data.products.identities import fingerprint, stable_id
from ja_media_data.storage.bronze import BronzeStore


CANONICALIZATION_POLICY_VERSION = "latest-manifest-modified-jpn-audio-v2"


@dataclass(frozen=True)
class CompiledCanonicalInputs:
    """Both tables comprising one atomically committed canonical product."""

    episodes: tuple[CanonicalEpisodeInput, ...]
    subtitles: tuple[CanonicalSubtitleInput, ...]
    fingerprint: str


def compile_product(
    repository: DuckLakeRepository, store: BronzeStore
) -> CompiledCanonicalInputs:
    """Select the latest effective capture per locator and enumerate subtitles."""

    selected = select_canonical_candidates(repository)
    product_fingerprint = fingerprint(
        CANONICALIZATION_POLICY_VERSION, [asdict(item) for item in selected]
    )
    episodes, subtitles = build_canonical_rows(
        selected,
        store,
        policy_version=CANONICALIZATION_POLICY_VERSION,
        fingerprint=fingerprint,
        stable_id=stable_id,
    )
    return CompiledCanonicalInputs(
        tuple(episodes), tuple(subtitles), product_fingerprint
    )


def binding_override_revision(repository: DuckLakeRepository) -> int:
    """Return the durable override-head revision used by build and cache keys."""

    source = repository.override_repository
    if source is None:
        return 0
    revision = getattr(source, "current_revision", None)
    if revision is not None:
        return int(revision())
    rows = [
        (
            item.override_id,
            item.namespace,
            item.series_id,
            item.episode,
            item.audio_capture_id,
        )
        for item in source.iter_current_overrides()
    ]
    return int(fingerprint(rows)[:15], 16)
