from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query

from ja_media_services.anilist_search.contracts import BulkSearchRequest
from ja_media_services.anilist_search.db import bulk_search, resolve_formats
from ja_media_services.anilist_search.responses import bulk_jsonl_response


def register_bulk_routes(app: FastAPI, app_state: Any) -> None:
    """Register local-only bulk title search routes."""

    @app.post("/search/bulk", response_model=None)
    async def bulk_search_endpoint(
        request: BulkSearchRequest,
        format: Literal["json", "jsonl"] = Query("json"),
    ):
        con = app_state.con
        if con is None:
            raise HTTPException(status_code=503, detail="Index not ready")
        if "force_anilist" in request.model_fields_set:
            raise HTTPException(
                status_code=400,
                detail="Bulk search is local-only; remove force_anilist and retry misses separately.",
            )

        formats = resolve_formats(
            request.include_movies,
            request.include_ova,
            request.all_formats,
        )
        with app_state._lock:
            results = bulk_search(con, request.queries, request.k, formats)
        if format == "jsonl":
            return bulk_jsonl_response(results)
        return {"results": results}
