from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time
from typing import Protocol

import duckdb

from ja_media_services.anilist_search.dataset import CSV_NAME, try_refresh_dataset
from ja_media_services.anilist_search.db import (
    get_row_count,
    rebuild_from_cached_csv,
)

logger = logging.getLogger("ja_media_services.anilist_search.refresh")


class DatabaseState(Protocol):
    """Mutable connection state shared by request handlers and refresh work."""

    con: duckdb.DuckDBPyConnection | None
    data_dir: Path | None
    db_path: Path | None
    _lock: threading.Lock


@dataclass
class RefreshStatus:
    """Mutable operational state for the background dataset refresh loop."""

    last_attempt_unix: float | None = None
    last_success_unix: float | None = None
    last_failure_unix: float | None = None
    last_failure: str | None = None
    consecutive_failures: int = 0
    last_update_unix: float | None = None
    last_index_rows: int | None = None

    def as_dict(self, *, stale_after_seconds: int) -> dict:
        now = time.time()
        last_success_age_seconds = None
        if self.last_success_unix is not None:
            last_success_age_seconds = round(now - self.last_success_unix, 3)
        return {
            "last_attempt_unix": self.last_attempt_unix,
            "last_success_unix": self.last_success_unix,
            "last_success_age_seconds": last_success_age_seconds,
            "last_failure_unix": self.last_failure_unix,
            "last_failure": self.last_failure,
            "consecutive_failures": self.consecutive_failures,
            "last_update_unix": self.last_update_unix,
            "last_index_rows": self.last_index_rows,
            "stale": (
                self.last_success_unix is None
                or (now - self.last_success_unix) > stale_after_seconds
            ),
        }


def background_refresh(
    state: DatabaseState,
    status: RefreshStatus,
    interval_seconds: int = 3600,
) -> None:
    """Poll Kaggle and publish rebuilt connections back to request handlers."""
    if state.data_dir is None or state.db_path is None:
        raise RuntimeError("AniList database paths are not initialized")
    csv_path = state.data_dir / CSV_NAME
    while True:
        time.sleep(interval_seconds)
        status.last_attempt_unix = time.time()
        try:
            updated = try_refresh_dataset(state.data_dir)
            with state._lock:
                if state.con is None:
                    raise RuntimeError("AniList database connection is not initialized")
                if updated:
                    row_count, state.con = rebuild_from_cached_csv(
                        csv_path, state.db_path, state.con
                    )
                    status.last_update_unix = time.time()
                else:
                    row_count = get_row_count(state.con)
            status.last_success_unix = time.time()
            status.last_failure = None
            status.consecutive_failures = 0
            status.last_index_rows = row_count
            logger.info(
                "AniList dataset refresh completed (updated=%s rows=%s)",
                updated,
                row_count,
            )
        except Exception as exc:
            status.last_failure_unix = time.time()
            status.last_failure = f"{type(exc).__name__}: {exc}"
            status.consecutive_failures += 1
            logger.exception(
                "AniList background dataset refresh failed (consecutive_failures=%d)",
                status.consecutive_failures,
            )
