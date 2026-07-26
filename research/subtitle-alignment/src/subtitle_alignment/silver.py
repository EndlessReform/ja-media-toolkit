"""Snapshot-pinned selection of correctly AniList-bound Silver locators."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random

import duckdb

from ja_media_data.lakehouse.time_travel import table_ref
from ja_media_data.products.lineage import MaterializationCatalog, ProductHead


@dataclass(frozen=True)
class EpisodeLocator:
    canonical_id: str
    anilist_id: int
    episode: int
    audio_capture_id: str
    binding_id: str
    binding_source: str
    manifest_bucket: str
    manifest_key: str
    manifest_etag: str
    manifest_modified_at: str
    input_fingerprint: str


@dataclass(frozen=True)
class EmbeddedSubtitleLocator:
    subtitle_input_id: str
    canonical_id: str
    anilist_id: int
    episode: int
    audio_capture_id: str
    object_bucket: str
    object_key: str
    stream_index: int
    codec: str | None
    declared_language: str | None
    input_fingerprint: str


@dataclass(frozen=True)
class SilverSelection:
    head: ProductHead
    eligible_series_count: int
    series_ids: tuple[int, ...]
    episodes: tuple[EpisodeLocator, ...]
    subtitles: tuple[EmbeddedSubtitleLocator, ...]

    def manifest_identity(self) -> dict[str, object]:
        """Return the exact durable product identity without result rows."""

        return {
            "materialization_id": self.head.materialization_id,
            "fingerprint": self.head.fingerprint,
            "recipe_revision": self.head.recipe_revision,
            "dagster_run_id": self.head.run_id,
            "snapshot_id": self.head.snapshot_id,
            "computed_at": str(self.head.computed_at),
        }


@dataclass(frozen=True)
class SilverPool:
    """Eligible AniList series and episode numbers at one Silver snapshot."""

    head: ProductHead
    series_episodes: dict[int, tuple[int, ...]]


def load_anilist_pool(
    connection: duckdb.DuckDBPyConnection,
    *,
    seed: int,
) -> SilverPool:
    """Load and deterministically order the eligible series draw pool."""

    head = MaterializationCatalog(connection).current_head("canonical_inputs")
    if head is None or head.snapshot_id is None:
        raise RuntimeError("canonical_inputs has no committed snapshot")
    episodes_ref = table_ref(
        "canonical_episode_inputs", snapshot_id=head.snapshot_id, alias="episode"
    )
    rows = connection.execute(
        f"""SELECT cast(series_id AS BIGINT), cast(episode AS INTEGER)
              FROM {episodes_ref}
             WHERE namespace = 'anilist'
               AND regexp_full_match(series_id, '[1-9][0-9]*')
               AND regexp_full_match(episode, '[1-9][0-9]*')
             ORDER BY 1, 2"""
    ).fetchall()
    grouped: dict[int, list[int]] = {}
    for series_id, episode in rows:
        grouped.setdefault(int(series_id), []).append(int(episode))
    ordered = draw_order(tuple(grouped), seed=seed)
    return SilverPool(
        head=head,
        series_episodes={
            series_id: tuple(sorted(set(grouped[series_id]))) for series_id in ordered
        },
    )


def apply_temporary_series_allowlist(
    pool: SilverPool, allowlist_path: Path
) -> SilverPool:
    """DO NOT MERGE: restrict Phase 0 while broken Bronze v1 is replaced.

    This deliberately fails closed. Deleting or renaming the local allowlist
    must never turn a supposedly filtered run into a full-corpus run.
    """

    if not allowlist_path.is_file():
        raise RuntimeError(
            "DO NOT MERGE temporary subtitle-series gate is missing: "
            f"{allowlist_path}"
        )
    allowed: set[int] = set()
    for line_number, raw in enumerate(allowlist_path.read_text().splitlines(), start=1):
        value = raw.partition("#")[0].strip()
        if not value:
            continue
        if not value.isascii() or not value.isdigit() or int(value) < 1:
            raise ValueError(
                f"invalid AniList ID at {allowlist_path}:{line_number}: {raw!r}"
            )
        allowed.add(int(value))
    if not allowed:
        raise RuntimeError(f"temporary series allowlist is empty: {allowlist_path}")
    filtered = {
        series_id: episodes
        for series_id, episodes in pool.series_episodes.items()
        if series_id in allowed
    }
    if not filtered:
        raise RuntimeError("temporary series allowlist matched no Silver series")
    return SilverPool(head=pool.head, series_episodes=filtered)


def load_selection(
    connection: duckdb.DuckDBPyConnection,
    *,
    pool: SilverPool,
    series_ids: tuple[int, ...],
) -> SilverSelection:
    """Load every episode and embedded track for the accepted series."""

    selected = tuple(sorted(series_ids))
    episodes_ref = table_ref(
        "canonical_episode_inputs", snapshot_id=pool.head.snapshot_id, alias="episode"
    )
    placeholders = ", ".join("?" for _ in selected)
    episode_rows = connection.execute(
        f"""SELECT canonical_id, cast(series_id AS BIGINT), cast(episode AS INTEGER),
                    audio_capture_id, binding_id, binding_source, manifest_bucket,
                    manifest_key, manifest_etag, manifest_modified_at,
                    input_fingerprint
               FROM {episodes_ref}
              WHERE namespace = 'anilist'
                AND cast(series_id AS BIGINT) IN ({placeholders})
                AND regexp_full_match(episode, '[1-9][0-9]*')
              ORDER BY cast(series_id AS BIGINT), cast(episode AS INTEGER)""",
        list(selected),
    ).fetchall()
    subtitles_ref = table_ref(
        "canonical_subtitle_inputs", snapshot_id=pool.head.snapshot_id, alias="subtitle"
    )
    subtitle_rows = connection.execute(
        f"""SELECT subtitle.subtitle_input_id, subtitle.canonical_id,
                    cast(subtitle.series_id AS BIGINT), cast(subtitle.episode AS INTEGER),
                    subtitle.audio_capture_id, subtitle.object_bucket,
                    subtitle.object_key, subtitle.stream_index, subtitle.codec,
                    subtitle.declared_language, subtitle.input_fingerprint
               FROM {subtitles_ref}
               JOIN {episodes_ref}
                 ON episode.canonical_id = subtitle.canonical_id
                AND episode.namespace = subtitle.namespace
                AND episode.series_id = subtitle.series_id
                AND episode.episode = subtitle.episode
              WHERE subtitle.namespace = 'anilist'
                AND cast(subtitle.series_id AS BIGINT) IN ({placeholders})
                AND regexp_full_match(subtitle.episode, '[1-9][0-9]*')
              ORDER BY cast(subtitle.series_id AS BIGINT),
                       cast(subtitle.episode AS INTEGER), subtitle.stream_index""",
        list(selected),
    ).fetchall()
    return SilverSelection(
        head=pool.head,
        eligible_series_count=len(pool.series_episodes),
        series_ids=selected,
        episodes=tuple(EpisodeLocator(*_stringify_time(row)) for row in episode_rows),
        subtitles=tuple(EmbeddedSubtitleLocator(*row) for row in subtitle_rows),
    )


def draw_order(eligible: tuple[int, ...], *, seed: int) -> tuple[int, ...]:
    """Return a deterministic random order independent of database ordering."""

    population = sorted(set(eligible))
    random.Random(seed).shuffle(population)
    return tuple(population)


def _stringify_time(row: tuple[object, ...]) -> tuple[object, ...]:
    values = list(row)
    values[9] = str(values[9])
    return tuple(values)
