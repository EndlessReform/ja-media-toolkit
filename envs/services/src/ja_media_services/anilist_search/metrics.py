from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from ja_media_services.anilist_search.db import RefreshStatus


def render_metrics(
    status: RefreshStatus,
    row_count: int,
) -> bytes:
    """Render AniList search operational state as Prometheus gauges.

    Three distinct timestamps are exposed:
    - **Check** (last_check_timestamp): set on every successful poll, even if
      upstream data has not changed.  Tells us the pipeline is alive.
    - **Rebuild** (last_rebuild_timestamp): when the currently served index was
      built, including the mandatory startup rebuild.
    - **Dataset update** (dataset_latest_update_timestamp): the greatest AniList
      ``updatedAt`` value represented in the current index.
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
        "Unix timestamp when the currently served index was built.",
        registry=registry,
    )
    dataset_latest_update = Gauge(
        "anilist_search_dataset_latest_update_timestamp",
        "Greatest AniList updatedAt timestamp represented in the current index.",
        registry=registry,
    )

    rows.set(float(row_count))
    consecutive_failures.set(float(status.consecutive_failures))

    # Check: last time a poll succeeded (regardless of data change)
    if status.last_success_unix is not None:
        last_check.set(float(status.last_success_unix))
    else:
        last_check.set(0.0)

    if status.last_rebuild_unix is not None:
        last_rebuild.set(float(status.last_rebuild_unix))
    else:
        last_rebuild.set(0.0)

    if status.dataset_latest_update_unix is not None:
        dataset_latest_update.set(float(status.dataset_latest_update_unix))
    else:
        dataset_latest_update.set(0.0)

    return generate_latest(registry)
