from __future__ import annotations

import time
from prometheus_client import CollectorRegistry, Gauge, generate_latest

from ja_media_services.anilist_search.db import RefreshStatus


def render_metrics(
    status: RefreshStatus,
    row_count: int,
) -> bytes:
    """Render AniList search operational state as Prometheus gauges.

    Two distinct signals are exposed:
    - **Check** (last_check_timestamp): set on every successful poll, even if
      upstream data has not changed.  Tells us the pipeline is alive.
    - **Rebuild** (last_rebuild_timestamp): set only when Kaggle delivered new
      data and the index was rebuilt.  Tells us the upstream project is alive.
    """

    registry = CollectorRegistry()

    rows = Gauge(
        "anilist_search_index_rows_total",
        "Number of anime rows in the current BM25 search index.",
        registry=registry,
    )
    consecutive_failures = Gauge(
        "anilist_search_consecutive_refresh_failures",
        "Consecutive background-refresh failures (0 = healthy).",
        registry=registry,
    )
    last_check = Gauge(
        "anilist_search_last_check_timestamp",
        "Unix timestamp of the last successful poll (check succeeded, data may "
        "not have changed).",
        registry=registry,
    )
    last_rebuild = Gauge(
        "anilist_search_last_rebuild_timestamp",
        "Unix timestamp of the last index rebuild from new upstream data.",
        registry=registry,
    )

    rows.set(float(row_count))
    consecutive_failures.set(float(status.consecutive_failures))

    # Check: last time a poll succeeded (regardless of data change)
    if status.last_success_unix is not None:
        last_check.set(float(status.last_success_unix))
    else:
        last_check.set(0.0)

    # Rebuild: only set when updated=True in background_refresh
    if status.last_update_unix is not None:
        last_rebuild.set(float(status.last_update_unix))
    else:
        last_rebuild.set(0.0)

    return generate_latest(registry)
