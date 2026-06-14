from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest


def render_metrics(metadata: dict[str, str]) -> bytes:
    """Render DB metadata as Prometheus gauges.

    This keeps v0 observability dependency-light. Request latency can be added
    later with ASGI middleware if the LAN service starts mattering operationally.
    """

    registry = CollectorRegistry()
    rows = Gauge(
        "anime_crosswalk_rows_total",
        "Number of anime rows in the generated crosswalk DB.",
        registry=registry,
    )
    lookups = Gauge(
        "anime_crosswalk_lookups_total",
        "Number of lookup rows in the generated crosswalk DB.",
        ["source"],
        registry=registry,
    )
    rebuild_success = Gauge(
        "anime_crosswalk_last_rebuild_success",
        "Whether the currently served generated DB passed validation.",
        registry=registry,
    )

    rows.set(float(metadata.get("anime_count", "0")))
    lookups.labels(source="all").set(float(metadata.get("lookup_count", "0")))
    for source in ("tvdb", "mal", "anidb", "tmdb"):
        lookups.labels(source=source).set(float(metadata.get(f"{source}_lookup_count", "0")))
    rebuild_success.set(1.0)
    return generate_latest(registry)
