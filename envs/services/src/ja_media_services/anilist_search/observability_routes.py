from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Response

from ja_media_services.anilist_search.db import get_row_count
from ja_media_services.anilist_search.observability import (
    fallback_snapshot,
    render_metrics,
)


def register_observability_routes(
    app: FastAPI,
    app_state: Any,
    *,
    logger: logging.Logger,
) -> None:
    """Register health and Prometheus endpoints for AniList search."""

    @app.get("/health", include_in_schema=False)
    @app.get("/healthz")
    def health() -> dict:
        return _service_state(app_state, logger=logger)

    @app.get("/metrics", response_class=Response)
    def metrics() -> Response:
        state = _service_state(app_state, logger=logger)
        body = render_metrics(
            rows=state["rows"],
            refresh=state["refresh"],
            fallback=state["fallback"],
        )
        return Response(body, media_type="text/plain; version=0.0.4")


def _service_state(app_state: Any, *, logger: logging.Logger) -> dict:
    con = app_state.con
    if con is None:
        raise HTTPException(status_code=503, detail="Index not ready")
    with app_state._lock:
        rows = get_row_count(con)
        fallback = fallback_snapshot(
            con,
            observer=app_state.fallback_observer,
            singleflight=app_state.exact_id_singleflight,
        )
    refresh = app_state.refresh_status.as_dict(
        stale_after_seconds=max(app_state.update_interval_seconds * 3, 1)
    )
    status = "degraded" if refresh["stale"] or refresh["consecutive_failures"] else "ok"
    if status == "degraded":
        logger.warning(
            "AniList search health is degraded (rows=%d refresh=%s)",
            rows,
            refresh,
        )
    return {"status": status, "rows": rows, "refresh": refresh, "fallback": fallback}
