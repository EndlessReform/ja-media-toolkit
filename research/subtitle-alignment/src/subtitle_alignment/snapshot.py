"""Build the immutable local Phase 0 subtitle dataset."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

import duckdb

from ja_media_core.kitsunekko import HttpKitsunekkoSubtitlesClient

from subtitle_alignment.access import DevReadAccess
from subtitle_alignment.objects import cache_embedded
from subtitle_alignment.sampling import qualify_series
from subtitle_alignment.silver import (
    SilverSelection,
    load_anilist_pool,
    load_selection,
)


def build_snapshot(
    *, cache_root: Path, series_count: int, seed: int, download_workers: int
) -> Path:
    """Freeze Silver locators and matching subtitle objects into local storage."""

    access = DevReadAccess.from_repository_config()
    connection = access.connect_catalog()
    try:
        pool = load_anilist_pool(connection, seed=seed)
    finally:
        connection.close()

    client = HttpKitsunekkoSubtitlesClient(timeout_s=20.0)
    health = client.health()
    stats_before = client.stats().values
    root = cache_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".phase0-building-", dir=root))
    try:
        accepted, draws, inventories, cached_candidates = qualify_series(
            temporary,
            client,
            pool,
            target_count=series_count,
            download_workers=download_workers,
        )
        connection = access.connect_catalog()
        try:
            selection = load_selection(connection, pool=pool, series_ids=accepted)
        finally:
            connection.close()
        embedded = cache_embedded(temporary, selection, access)
        stats_after = client.stats().values
        if stats_after != stats_before:
            raise RuntimeError("Kitsunekko source changed while snapshotting; retry")
        dataset_id = _dataset_id(selection, seed=seed, mirror=stats_before)
        target = root / dataset_id
        if target.exists():
            raise FileExistsError(f"immutable dataset already exists: {target}")
        _write_database(
            temporary,
            selection=selection,
            embedded=embedded,
            inventories=inventories,
            candidates=cached_candidates,
            draws=draws,
        )
        manifest = {
            "schema_version": 1,
            "dataset_id": dataset_id,
            "created_at": datetime.now(UTC).isoformat(),
            "seed": seed,
            "silver": selection.manifest_identity(),
            "kitsunekko": {
                "health": {
                    "ok": health.get("ok"),
                    "ingest_phase": health.get("ingest_phase"),
                    "mirror_commit": health.get("mirror_commit"),
                    "crosswalk_source_commit": health.get("crosswalk_source_commit"),
                },
                "stats": stats_after,
            },
            "series_ids": list(selection.series_ids),
            "counts": {
                "eligible_anilist_series": selection.eligible_series_count,
                "drawn_series": len(draws),
                "rejected_series": sum(
                    item["decision"] == "rejected" for item in draws
                ),
                "selected_series": len(selection.series_ids),
                "canonical_episodes": len(selection.episodes),
                "embedded_subtitles": len(embedded),
                "kitsunekko_inventory_files": sum(
                    len(items) for items in inventories.values()
                ),
                "kitsunekko_episode_candidates": len(cached_candidates),
                "kitsunekko_cached_candidates": sum(
                    item["fetch_status"] == "ok" for item in cached_candidates
                ),
                "kitsunekko_unavailable_candidates": sum(
                    item["fetch_status"] != "ok" for item in cached_candidates
                ),
                "kitsunekko_missing_episodes": sum(
                    int(item["missing_episode_count"])
                    for item in draws
                    if item["decision"] == "accepted"
                ),
            },
            "series_draws": draws,
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(json.dumps(manifest["counts"], sort_keys=True))
    return target


def _write_database(
    root: Path,
    *,
    selection: SilverSelection,
    embedded: list[dict[str, object]],
    inventories: dict[int, list[dict[str, Any]]],
    candidates: list[dict[str, object]],
    draws: list[dict[str, object]],
) -> None:
    connection = duckdb.connect(str(root / "evaluation.duckdb"))
    try:
        _create_table(
            connection, "episodes", [asdict(item) for item in selection.episodes]
        )
        _create_table(connection, "embedded_subtitles", embedded)
        inventory_rows = [
            {
                "anilist_id": series_id,
                "file_count": len(files),
                "inventory_json": json.dumps(files, ensure_ascii=False, sort_keys=True),
            }
            for series_id, files in inventories.items()
        ]
        _create_table(connection, "kitsunekko_inventories", inventory_rows)
        _create_table(connection, "kitsunekko_candidates", candidates)
        _create_table(connection, "series_draws", draws)
    finally:
        connection.close()


def _create_table(
    connection: duckdb.DuckDBPyConnection,
    name: str,
    values: list[dict[str, object]],
) -> None:
    if not values:
        connection.execute(f"CREATE TABLE {name} (empty BOOLEAN)")
        return
    columns = list(values[0])
    placeholders = ", ".join("?" for _ in columns)
    definitions = ", ".join(f'"{column}" VARCHAR' for column in columns)
    connection.execute(f"CREATE TABLE {name} ({definitions})")
    connection.executemany(
        f"INSERT INTO {name} VALUES ({placeholders})",
        [[_db_value(item[column]) for column in columns] for item in values],
    )


def _db_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _dataset_id(
    selection: SilverSelection, *, seed: int, mirror: dict[str, str]
) -> str:
    payload = json.dumps(
        {
            "materialization": selection.head.materialization_id,
            "snapshot": selection.head.snapshot_id,
            "seed": seed,
            "series": selection.series_ids,
            "kitsunekko": mirror.get("mirror_commit"),
            "sample_policy": "multi-episode-kitsunekko-75pct-v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "phase0-" + hashlib.sha256(payload.encode()).hexdigest()[:16]
