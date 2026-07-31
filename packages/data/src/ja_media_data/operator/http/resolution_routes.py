"""Localhost HTML and native POST/SSE routes for resolution review."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from ja_media_data.operator.http.dependencies import get_runtime
from ja_media_data.operator.http.forms import bounded_form, optional_int
from ja_media_data.operator.http.resolution_views import (
    history_context,
    rail_context,
    resolved_workspace_context,
    workspace_context,
)
from ja_media_data.operator.resolution_review.provider import ModelChoice
from ja_media_data.operator.resolution_review.streaming import (
    ResolutionReviewSession,
    ReviewEvent,
)
from ja_media_data.operator.runtime import OperatorRuntime


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(prefix="/operator/resolution-review", include_in_schema=False)


@router.get("", response_class=HTMLResponse)
def resolution_review_page(
    request: Request,
    view: Literal["pending", "resolved"] = Query(default="pending"),
    offset: int = Query(default=0, ge=0),
    anilist_id: int | None = Query(default=None, ge=1),
    runtime: OperatorRuntime = Depends(get_runtime),
) -> HTMLResponse:
    """Render the series rail and one selected review workspace."""

    rail = rail_context(runtime, view=view, offset=offset, selected=anilist_id)
    page = rail["series_page"]
    selected = anilist_id or (page.items[0].anilist_id if page.items else None)
    workspace = (
        resolved_workspace_context(runtime, selected)
        if view == "resolved"
        else workspace_context(runtime, selected, issue_offset=0)
    )
    return templates.TemplateResponse(
        request=request,
        name="resolution_review.html",
        context={
            **rail,
            "selected_anilist_id": selected,
            **history_context(runtime, series_view=view),
            **workspace,
        },
    )


@router.get("/series/{anilist_id}", response_class=HTMLResponse)
def resolution_series(
    request: Request,
    anilist_id: int,
    issue_offset: int = Query(default=0, ge=0),
    runtime: OperatorRuntime = Depends(get_runtime),
) -> HTMLResponse:
    """Swap the issue list and agent panel for one selected series."""

    return templates.TemplateResponse(
        request=request,
        name="_resolution_workspace.html",
        context=workspace_context(runtime, anilist_id, issue_offset=issue_offset),
    )


@router.get("/resolved/{anilist_id}", response_class=HTMLResponse)
def resolved_series(
    request: Request,
    anilist_id: int,
    runtime: OperatorRuntime = Depends(get_runtime),
) -> HTMLResponse:
    """Show active applied crosswalks and their deterministic reversal controls."""

    return templates.TemplateResponse(
        request=request,
        name="_resolution_resolved_workspace.html",
        context=resolved_workspace_context(runtime, anilist_id),
    )


@router.post("/series/{anilist_id}/run")
async def start_resolution_review(
    request: Request,
    anilist_id: int,
    runtime: OperatorRuntime = Depends(get_runtime),
) -> StreamingResponse:
    """Start a model run and stream server-rendered semantic events."""

    form = await bounded_form(request)
    try:
        session = runtime.start_review(
            anilist_id,
            ModelChoice(
                model_id=form.get("model_id"),
                base_url=form.get("base_url"),
                api_key=form.get("api_key"),
                max_turns=optional_int(
                    form.get("max_turns"), field="max_turns", minimum=1, maximum=30
                ),
            ),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _stream(
        runtime,
        session,
        session.start(),
        reset=True,
        status_message="Model and tools are working…",
    )


@router.post("/paused/{token}/accept")
async def accept_resolution_review(
    token: str,
    runtime: OperatorRuntime = Depends(get_runtime),
) -> StreamingResponse:
    """Consume one approval token and resume the same agent run."""

    try:
        session = runtime.take_paused_review(token)
    except KeyError as error:
        return _stream_error(str(error))
    return _stream(
        runtime,
        session,
        session.approve(),
        reset=False,
        status_message="Applying approved draft…",
    )


@router.post("/paused/{token}/reject")
async def reject_resolution_review(
    request: Request,
    token: str,
    runtime: OperatorRuntime = Depends(get_runtime),
) -> StreamingResponse:
    """Consume one approval token, pass the reason to the model, and resume."""

    try:
        session = runtime.take_paused_review(token)
    except KeyError as error:
        return _stream_error(str(error))
    reason = (await bounded_form(request)).get("reason", "").strip()
    if not reason:
        return _stream_error("a rejection reason is required")
    return _stream(
        runtime,
        session,
        session.reject(reason),
        reset=False,
        status_message="Model is revising the draft…",
    )


@router.post("/history/{batch_id}/reverse", response_class=HTMLResponse)
async def reverse_resolution_batch(
    request: Request,
    batch_id: str,
    runtime: OperatorRuntime = Depends(get_runtime),
) -> HTMLResponse:
    """Deterministically restore a batch's prior control heads."""

    form = await bounded_form(request)
    reason = form.get("reason", "").strip()
    series_view = form.get("series_view", "pending")
    if series_view not in {"pending", "resolved"}:
        series_view = "pending"
    selected = optional_int(form.get("anilist_id"), field="anilist_id", minimum=1)
    try:
        runtime.reverse_resolution(batch_id, reason=reason)
        context = {
            **history_context(runtime, series_view=series_view),
            "history_message": f"Reversed {batch_id}.",
        }
    except (LookupError, RuntimeError, ValueError) as error:
        context = {
            **history_context(runtime, series_view=series_view),
            "history_error": str(error),
        }
    context.update(rail_context(runtime, view=series_view, offset=0, selected=selected))
    if series_view == "resolved" and selected is not None:
        context.update(resolved_workspace_context(runtime, selected))
        context["refresh_resolved_workspace"] = True
    return templates.TemplateResponse(
        request=request, name="_resolution_reverse_result.html", context=context
    )


def _stream(
    runtime: OperatorRuntime,
    session: ResolutionReviewSession,
    events: AsyncIterator[ReviewEvent],
    *,
    reset: bool,
    status_message: str,
) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        yield _sse(
            _render(
                "_resolution_stream_start.html",
                reset=reset,
                status_message=status_message,
            )
        )
        try:
            async for event in events:
                if event.kind == "approval_required":
                    token = runtime.pause_review(session)
                    html = _render(
                        "_resolution_proposal.html",
                        token=token,
                        proposal=event.data["proposal"],
                        review_mode=getattr(runtime, "review_mode", "local"),
                    )
                else:
                    html = _render(
                        "_resolution_event.html",
                        event=event,
                        review_mode=getattr(runtime, "review_mode", "local"),
                    )
                    if event.kind == "completed" and event.data.get("result", {}).get(
                        "durable_writeback"
                    ):
                        html += _render(
                            "_resolution_history_swap.html",
                            **history_context(runtime),
                        )
                        html += _render(
                            "_resolution_rail_swap.html",
                            **rail_context(
                                runtime,
                                view="pending",
                                offset=0,
                                selected=None,
                            ),
                        )
                yield _sse(html)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            yield _sse(_render("_resolution_error.html", error=str(error)))

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _stream_error(message: str) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        yield _sse(_render("_resolution_error.html", error=message))

    return StreamingResponse(body(), media_type="text/event-stream")


def _render(name: str, **context: object) -> str:
    return templates.get_template(name).render(**context)


def _sse(html: str) -> str:
    return "".join(f"data: {line}\n" for line in html.splitlines()) + "\n"
