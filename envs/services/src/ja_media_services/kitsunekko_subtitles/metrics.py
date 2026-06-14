from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest


def render_metrics(metadata: dict[str, str]) -> bytes:
    """Render Kitsunekko subtitle DB metadata as Prometheus gauges."""

    registry = CollectorRegistry()
    rows = Gauge(
        "kitsunekko_subtitle_files_total",
        "Number of subtitle files in the generated Kitsunekko DB.",
        registry=registry,
    )
    lookups = Gauge(
        "kitsunekko_subtitle_lookups_total",
        "Number of lookup rows in the generated Kitsunekko DB.",
        registry=registry,
    )
    rebuild_success = Gauge(
        "kitsunekko_subtitles_last_rebuild_success",
        "Whether the currently served generated DB passed validation.",
        registry=registry,
    )

    rows.set(float(metadata.get("subtitle_row_count", "0")))
    lookups.set(float(metadata.get("lookup_row_count", "0")))
    rebuild_success.set(1.0)
    return generate_latest(registry)
