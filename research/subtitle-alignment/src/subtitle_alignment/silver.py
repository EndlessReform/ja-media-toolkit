"""Snapshot-pinned selection of correctly AniList-bound Silver locators."""

from __future__ import annotations

from dataclasses import dataclass
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
    audio_object_bucket: str
    audio_object_key: str
    audio_stream_index: int
    audio_codec: str | None
    audio_declared_language: str | None
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
                    audio_object_bucket, audio_object_key, audio_stream_index,
                    audio_codec, audio_declared_language,
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
