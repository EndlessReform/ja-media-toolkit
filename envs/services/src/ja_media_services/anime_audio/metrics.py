"""Prometheus rendering for the indexed anime-audio service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from prometheus_client import CollectorRegistry, Gauge, generate_latest


def render_metrics(state: dict[str, Any]) -> bytes:
    """Render current index and reconciliation state without path labels."""

    registry = CollectorRegistry()
    values = (
        ("anime_audio_index_ready", "Whether the index can serve reads.", int(state["ready"])),
        ("anime_audio_series_total", "Indexed anime series.", state["series_count"]),
        ("anime_audio_artifacts_total", "Indexed audio artifacts.", state["artifact_count"]),
        (
            "anime_audio_reconciliation_errors",
            "Current manifest or artifact reconciliation errors.",
            state["error_count"],
        ),
        (
            "anime_audio_last_reconciliation_timestamp_seconds",
            "Unix timestamp of the last successful reconciliation.",
            _timestamp(state["last_success"]),
        ),
    )
    for name, help_text, value in values:
        Gauge(name, help_text, registry=registry).set(float(value))
    return generate_latest(registry)


def _timestamp(value: str | None) -> float:
    return datetime.fromisoformat(value).timestamp() if value else 0.0
