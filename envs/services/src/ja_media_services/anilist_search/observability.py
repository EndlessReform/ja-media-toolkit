from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any

import duckdb
from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from ja_media_services.anilist_search.fallback_schema import ANIME_TABLE
from ja_media_services.anilist_search.singleflight import ExactIdSingleFlight


@dataclass
class FallbackObserver:
    """Process-local counters for direct AniList fallback behavior."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    exact_requests: int = 0
    exact_cache_hits: int = 0
    exact_cache_misses: int = 0
    search_requests: int = 0
    search_cache_hits: int = 0
    search_cache_misses: int = 0
    outbound_requests: int = 0
    outbound_429s: int = 0
    outbound_errors: int = 0

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + amount)

    def counters(self) -> dict[str, int]:
        with self._lock:
            return {
                "exact_requests": self.exact_requests,
                "exact_cache_hits": self.exact_cache_hits,
                "exact_cache_misses": self.exact_cache_misses,
                "search_requests": self.search_requests,
                "search_cache_hits": self.search_cache_hits,
                "search_cache_misses": self.search_cache_misses,
                "outbound_requests": self.outbound_requests,
                "outbound_429s": self.outbound_429s,
                "outbound_errors": self.outbound_errors,
            }


def fallback_snapshot(
    con: duckdb.DuckDBPyConnection,
    *,
    observer: FallbackObserver,
    singleflight: ExactIdSingleFlight,
    now: float | None = None,
) -> dict[str, Any]:
    """Return cache state plus process-local fallback counters for health."""

    timestamp = time.time() if now is None else now
    counts = con.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE expires_at_unix <= ?),
            COUNT(*) FILTER (WHERE negative)
        FROM {ANIME_TABLE}
        """,
        [timestamp],
    ).fetchone()
    counters = observer.counters()
    exact_total = counters["exact_cache_hits"] + counters["exact_cache_misses"]
    search_total = counters["search_cache_hits"] + counters["search_cache_misses"]
    return {
        "cached_rows": int(counts[0]),
        "expired_rows": int(counts[1]),
        "negative_rows": int(counts[2]),
        "inflight_exact_ids": singleflight.inflight_count,
        "exact_coalesced_waits": singleflight.coalesced_waits,
        "exact_hit_rate": _rate(counters["exact_cache_hits"], exact_total),
        "search_hit_rate": _rate(counters["search_cache_hits"], search_total),
        **counters,
    }


def render_metrics(*, rows: int, refresh: dict[str, Any], fallback: dict[str, Any]) -> bytes:
    """Render AniList search service metrics in Prometheus exposition format."""

    registry = CollectorRegistry()
    Gauge("anilist_search_rows_total", "Rows in the local AniList search index.", registry=registry).set(rows)
    Gauge(
        "anilist_search_refresh_consecutive_failures",
        "Consecutive background refresh failures.",
        registry=registry,
    ).set(float(refresh["consecutive_failures"]))
    Gauge(
        "anilist_search_refresh_last_success_timestamp_seconds",
        "Unix timestamp of the last successful index refresh.",
        registry=registry,
    ).set(float(refresh["last_success_unix"] or 0))
    for key in ("cached_rows", "expired_rows", "negative_rows", "inflight_exact_ids"):
        Gauge(
            f"anilist_search_fallback_{key}",
            f"AniList fallback {key.replace('_', ' ')}.",
            registry=registry,
        ).set(float(fallback[key]))
    requests = Counter(
        "anilist_search_fallback_requests",
        "Fallback requests.",
        ["kind"],
        registry=registry,
    )
    requests.labels(kind="exact").inc(fallback["exact_requests"])
    requests.labels(kind="search").inc(fallback["search_requests"])
    hits = Counter(
        "anilist_search_fallback_cache_hits",
        "Fallback cache hits.",
        ["kind"],
        registry=registry,
    )
    hits.labels(kind="exact").inc(fallback["exact_cache_hits"])
    hits.labels(kind="search").inc(fallback["search_cache_hits"])
    misses = Counter(
        "anilist_search_fallback_cache_misses",
        "Fallback cache misses.",
        ["kind"],
        registry=registry,
    )
    misses.labels(kind="exact").inc(fallback["exact_cache_misses"])
    misses.labels(kind="search").inc(fallback["search_cache_misses"])
    _plain_counter(registry, "outbound_requests", "Outbound AniList GraphQL requests.", fallback)
    _plain_counter(registry, "outbound_429s", "AniList 429 responses.", fallback)
    _plain_counter(registry, "outbound_errors", "Outbound AniList errors.", fallback)
    _plain_counter(registry, "exact_coalesced_waits", "Exact-ID requests coalesced.", fallback)
    return generate_latest(registry)


def _plain_counter(
    registry: CollectorRegistry,
    key: str,
    help_text: str,
    fallback: dict[str, Any],
) -> None:
    Counter(f"anilist_search_fallback_{key}", help_text, registry=registry).inc(fallback[key])


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
