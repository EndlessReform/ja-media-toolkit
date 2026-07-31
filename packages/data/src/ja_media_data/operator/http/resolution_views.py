"""Server-rendered view models for the resolution-review HTTP adapter."""

from __future__ import annotations

from ja_media_data.operator.runtime import OperatorRuntime
from ja_media_data.settings import get_settings


def workspace_context(
    runtime: OperatorRuntime, anilist_id: int | None, *, issue_offset: int
) -> dict[str, object]:
    settings = get_settings().agent
    issues = []
    if anilist_id is not None:
        context = runtime.review_context(anilist_id)
        issues = context.toolbox.issues(anilist_id, issue_offset, 50)
    return {
        "selected_anilist_id": anilist_id,
        "issues": issues,
        "issue_offset": issue_offset,
        "model_id": settings.model_id or "",
        "base_url": settings.base_url or "",
        "max_turns": settings.max_turns,
        "review_mode": getattr(runtime, "review_mode", "local"),
    }


def resolved_workspace_context(
    runtime: OperatorRuntime, anilist_id: int | None
) -> dict[str, object]:
    batches = runtime.active_resolutions(anilist_id) if anilist_id is not None else ()
    return {
        "selected_anilist_id": anilist_id,
        "active_resolution_batches": batches,
        "review_mode": getattr(runtime, "review_mode", "local"),
    }


def rail_context(
    runtime: OperatorRuntime,
    *,
    view: str,
    offset: int,
    selected: int | None,
) -> dict[str, object]:
    page = runtime.review_series(view=view, offset=offset, limit=50)
    other_view = "resolved" if view == "pending" else "pending"
    other = runtime.review_series(view=other_view, offset=0, limit=1)
    return {
        "series_page": page,
        "series_view": view,
        "selected_anilist_id": selected,
        "pending_total": page.total if view == "pending" else other.total,
        "resolved_total": page.total if view == "resolved" else other.total,
    }


def history_context(
    runtime: OperatorRuntime, *, series_view: str = "pending"
) -> dict[str, object]:
    history = (
        runtime.resolution_history(limit=50)
        if hasattr(runtime, "resolution_history")
        else ()
    )
    return {
        "decision_history": history,
        "review_mode": getattr(runtime, "review_mode", "local"),
        "series_view": series_view,
    }
