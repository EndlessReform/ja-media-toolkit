from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from anime_audio_support import manifest, write_series
from ja_media_services.anime_audio.db import fetch_series, initialize, stats
from ja_media_services.anime_audio.index import (
    incremental_scan,
    reconcile,
    refresh_manifest,
)


def test_refresh_replaces_only_changed_series(tmp_path: Path) -> None:
    library = tmp_path / "library"
    first = write_series(library)
    write_series(
        library,
        directory="anilist-2",
        payload=manifest(anilist_id=2, title="Untouched"),
    )
    connection = initialize(tmp_path / "index.sqlite")
    reconcile(connection, library)

    first.write_text(json.dumps(manifest(title="Replacement")), encoding="utf-8")
    assert refresh_manifest(connection, library, first)

    assert fetch_series(connection, 1)["title"] == "Replacement"  # type: ignore[index]
    assert fetch_series(connection, 2)["title"] == "Untouched"  # type: ignore[index]


def test_refresh_deletion_removes_only_its_series(tmp_path: Path) -> None:
    library = tmp_path / "library"
    first = write_series(library)
    write_series(
        library, directory="anilist-2", payload=manifest(anilist_id=2)
    )
    connection = initialize(tmp_path / "index.sqlite")
    reconcile(connection, library)

    first.unlink()
    assert refresh_manifest(connection, library, first)

    assert fetch_series(connection, 1) is None
    assert fetch_series(connection, 2) is not None


def test_invalid_changed_manifest_removes_stale_rows_and_records_failure(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    path = write_series(library)
    connection = initialize(tmp_path / "index.sqlite")
    reconcile(connection, library)

    path.write_text("{broken", encoding="utf-8")
    assert not refresh_manifest(connection, library, path)

    assert fetch_series(connection, 1) is None
    state = stats(connection)
    assert state["error_count"] == 1
    assert state["refresh_failures"] == 1


def test_incremental_scan_repairs_missed_replacement_and_deletion(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library"
    first = write_series(library)
    second = write_series(
        library, directory="anilist-2", payload=manifest(anilist_id=2)
    )
    connection = initialize(tmp_path / "index.sqlite")
    reconcile(connection, library)

    first.write_text(json.dumps(manifest(title="Repaired")), encoding="utf-8")
    os.utime(first, ns=(first.stat().st_atime_ns, first.stat().st_mtime_ns + 1))
    second.unlink()
    result = incremental_scan(connection, library)

    assert result.refreshed_count == 1
    assert result.removed_count == 1
    assert fetch_series(connection, 1)["title"] == "Repaired"  # type: ignore[index]
    assert fetch_series(connection, 2) is None


def test_incremental_scan_does_not_parse_unchanged_valid_or_invalid_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    write_series(library)
    invalid = write_series(
        library, directory="anilist-2", payload=manifest(anilist_id=2)
    )
    invalid.write_text("{broken", encoding="utf-8")
    connection = initialize(tmp_path / "index.sqlite")
    reconcile(connection, library)

    def fail_if_parsed(*_: object) -> object:
        raise AssertionError("unchanged manifest was parsed")

    monkeypatch.setattr(
        "ja_media_services.anime_audio.manifest.manifest_from_mapping",
        fail_if_parsed,
    )
    result = incremental_scan(connection, library)

    assert result.unchanged_count == 2
    assert result.refreshed_count == 0


def test_restart_uses_persisted_manifest_metadata(tmp_path: Path) -> None:
    library = tmp_path / "library"
    write_series(library)
    db_path = tmp_path / "index.sqlite"
    first = initialize(db_path)
    reconcile(first, library)
    first.close()

    restarted = initialize(db_path)
    result = incremental_scan(restarted, library)

    assert result.unchanged_count == 1
    assert result.refreshed_count == 0


def test_incomplete_scan_never_removes_existing_rows(tmp_path: Path) -> None:
    library = tmp_path / "library"
    write_series(library)
    connection = initialize(tmp_path / "index.sqlite")
    reconcile(connection, library)

    library.rename(tmp_path / "offline")
    with pytest.raises(FileNotFoundError):
        incremental_scan(connection, library)

    assert fetch_series(connection, 1) is not None
    assert stats(connection)["incremental_scan_failures"] == 1
