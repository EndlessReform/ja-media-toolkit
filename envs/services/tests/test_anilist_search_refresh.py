from __future__ import annotations

import time
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from ja_media_services.anilist_search import dataset, db, refresh


def cached_dataset(tmp_path: Path, revision: str, content: str) -> Path:
    path = tmp_path / "cache" / "versions" / revision / dataset.CSV_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_ensure_dataset_publishes_cached_revision_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = cached_dataset(tmp_path, "12", "current dataset")
    data_dir = tmp_path / "data"
    monkeypatch.setattr(
        dataset.kagglehub,
        "dataset_download",
        lambda *args, **kwargs: str(cache_path),
    )

    csv_path = dataset.ensure_dataset(data_dir)

    assert csv_path.read_text(encoding="utf-8") == "current dataset"
    assert (data_dir / dataset.REVISION_NAME).read_text(encoding="utf-8") == "12"
    assert not csv_path.with_suffix(f"{csv_path.suffix}.tmp").exists()


def test_refresh_adopts_revision_for_matching_legacy_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = cached_dataset(tmp_path, "12", "same dataset")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / dataset.CSV_NAME).write_text("same dataset", encoding="utf-8")
    monkeypatch.setattr(
        dataset.kagglehub,
        "dataset_download",
        lambda *args, **kwargs: str(cache_path),
    )

    assert dataset.try_refresh_dataset(data_dir) is False
    assert (data_dir / dataset.REVISION_NAME).read_text(encoding="utf-8") == "12"


def test_refresh_replaces_csv_when_kaggle_revision_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = cached_dataset(tmp_path, "13", "new dataset")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / dataset.CSV_NAME).write_text("old dataset", encoding="utf-8")
    (data_dir / dataset.REVISION_NAME).write_text("12", encoding="utf-8")
    monkeypatch.setattr(
        dataset.kagglehub,
        "dataset_download",
        lambda *args, **kwargs: str(cache_path),
    )

    assert dataset.try_refresh_dataset(data_dir) is True
    assert (data_dir / dataset.CSV_NAME).read_text(encoding="utf-8") == "new dataset"
    assert (data_dir / dataset.REVISION_NAME).read_text(encoding="utf-8") == "13"


def test_refresh_does_not_pass_durable_output_directory_to_kagglehub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = cached_dataset(tmp_path, "13", "current dataset")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / dataset.CSV_NAME).write_text("current dataset", encoding="utf-8")
    (data_dir / dataset.REVISION_NAME).write_text("13", encoding="utf-8")

    def download(*args: object, **kwargs: object) -> str:
        assert "output_dir" not in kwargs
        assert kwargs.get("force_download") is not True
        return str(cache_path)

    monkeypatch.setattr(dataset.kagglehub, "dataset_download", download)

    assert dataset.try_refresh_dataset(data_dir) is False


def test_refresh_status_marks_stale_after_missed_refresh_window() -> None:
    status = refresh.RefreshStatus(
        last_success_unix=time.time() - 400,
        consecutive_failures=2,
    )

    payload = status.as_dict(stale_after_seconds=300)

    assert payload["stale"] is True
    assert payload["consecutive_failures"] == 2


def test_startup_retries_transient_duckdb_fts_transaction_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    reopened_connection = object()

    def build_index(*args: object, **kwargs: object) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise db.duckdb.TransactionException("transient FTS catalog failure")
        return 20_346

    class Connection:
        closed = False

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(db, "build_index", build_index)
    monkeypatch.setattr(db, "open_db", lambda path: reopened_connection)

    rows, active_connection = db.rebuild_from_cached_csv(
        tmp_path / dataset.CSV_NAME,
        tmp_path / "anime_index.db",
        connection,  # type: ignore[arg-type]
    )

    assert rows == 20_346
    assert attempts == 2
    assert connection.closed is True
    assert active_connection is reopened_connection


def test_background_refresh_publishes_reopened_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_connection = object()
    new_connection = object()
    state = SimpleNamespace(
        con=old_connection,
        data_dir=tmp_path,
        db_path=tmp_path / "anime_index.db",
        _lock=threading.Lock(),
    )
    status = refresh.RefreshStatus()
    sleeps = 0

    def sleep_once(seconds: int) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise StopIteration

    monkeypatch.setattr(refresh.time, "sleep", sleep_once)
    monkeypatch.setattr(refresh, "try_refresh_dataset", lambda path: True)
    monkeypatch.setattr(
        refresh,
        "rebuild_from_cached_csv",
        lambda csv_path, db_path, con: (20_346, new_connection),
    )

    with pytest.raises(StopIteration):
        refresh.background_refresh(state, status, interval_seconds=1)

    assert state.con is new_connection
    assert status.last_index_rows == 20_346
    assert status.consecutive_failures == 0
