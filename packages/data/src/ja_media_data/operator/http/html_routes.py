"""Server-rendered navigation and HTMX fragments over application DTOs."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ja_media_data.operator.application import OperatorApplication
from ja_media_data.operator.http.dependencies import get_application
from ja_media_data.operator.models import CampaignSnapshot


templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(prefix="/operator", include_in_schema=False)


@router.get("", response_class=HTMLResponse)
def workboard(
    request: Request,
    application: OperatorApplication = Depends(get_application),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="workboard.html",
        context={"campaigns": application.list_campaigns()},
    )


@router.get("/runs", response_class=HTMLResponse)
def run_log(
    request: Request,
    application: OperatorApplication = Depends(get_application),
) -> HTMLResponse:
    """Render bounded durable invocation history for human inspection."""

    return templates.TemplateResponse(
        request=request,
        name="runs.html",
        context={"runs": application.list_runs(limit=100)},
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(
    request: Request,
    run_id: str,
    application: OperatorApplication = Depends(get_application),
) -> HTMLResponse:
    """Explain one Dagster run and each step execution it contains."""

    try:
        run = application.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    return templates.TemplateResponse(
        request=request, name="run.html", context={"run": run}
    )


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_page(
    request: Request,
    campaign_id: str,
    series_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    application: OperatorApplication = Depends(get_application),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="campaign.html",
        context=_campaign_context(application, campaign_id, series_id, run_id),
    )


@router.get("/campaigns/{campaign_id}/lens", response_class=HTMLResponse)
def campaign_lens(
    request: Request,
    campaign_id: str,
    series_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    application: OperatorApplication = Depends(get_application),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="_canonicalization_lens.html",
        context=_campaign_context(application, campaign_id, series_id, run_id),
    )


@router.get("/campaigns/{campaign_id}/products", response_class=HTMLResponse)
def campaign_products(
    request: Request,
    campaign_id: str,
    series_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    run_id: str | None = Query(default=None),
    application: OperatorApplication = Depends(get_application),
) -> HTMLResponse:
    """Render one bounded page of the campaign's conclusion product."""

    normalized = series_id.strip() or None if series_id is not None else None
    try:
        snapshot = application.get_product_snapshot(
            campaign_id, series_id=normalized, offset=offset, limit=limit,
            run_id=run_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="campaign or run not found") from error
    return templates.TemplateResponse(
        request=request,
        name="_canonical_product.html",
        context={"snapshot": snapshot, "product_peek": False},
    )


@router.get("/campaigns/{campaign_id}/stages/{stage}", response_class=HTMLResponse)
def stage_results(
    request: Request,
    campaign_id: str,
    stage: str,
    series_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    run_id: str | None = Query(default=None),
    application: OperatorApplication = Depends(get_application),
) -> HTMLResponse:
    """Lazily render one server-paged, stage-owned result table."""

    normalized = series_id.strip() or None if series_id is not None else None
    try:
        page = application.get_stage_results(
            campaign_id, stage, series_id=normalized, offset=offset, limit=limit,
            run_id=run_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="stage not found") from error
    return templates.TemplateResponse(
        request=request,
        name="_stage_results.html",
        context={"page": page, "campaign_id": campaign_id, "run_id": run_id or ""},
    )


@router.get(
    "/campaigns/{campaign_id}/candidates/{namespace}/{series_id}/{episode}",
    response_class=HTMLResponse,
)
def binding_candidates(
    request: Request, campaign_id: str, namespace: str, series_id: str,
    episode: str, run_id: str | None = Query(default=None),
    application: OperatorApplication = Depends(get_application),
) -> HTMLResponse:
    """Lazily render the evidence rows for exactly one binding locator."""

    locator = namespace, series_id, episode
    candidates = application.get_candidates(
        campaign_id, locator, run_id=run_id
    )
    return templates.TemplateResponse(
        request=request, name="_candidate_rows.html",
        context={"candidates": candidates, "locator_dom_id": "-".join(locator)},
    )


def _campaign_context(
    application: OperatorApplication,
    campaign_id: str,
    series_id: str | None,
    run_id: str | None,
) -> dict[str, object]:
    normalized = series_id.strip() or None if series_id is not None else None
    snapshot = _snapshot(
        application, campaign_id, series_id=normalized, gate_limit=3,
        run_id=run_id,
    )
    initial_stage = application.get_stage_results(
        campaign_id, "episode_resolution", series_id=normalized, limit=50,
        run_id=run_id,
    )
    return {
        "snapshot": snapshot,
        "series_id": normalized or "",
        "product_peek": True,
        "initial_stage_page": initial_stage,
        "run_id": run_id or "",
    }


def _snapshot(
    application: OperatorApplication,
    campaign_id: str,
    *,
    series_id: str | None,
    gate_offset: int = 0,
    gate_limit: int = 50,
    run_id: str | None = None,
) -> CampaignSnapshot:
    series_id = series_id.strip() or None if series_id is not None else None
    try:
        return application.get_campaign_snapshot(
            campaign_id,
            series_id=series_id,
            gate_offset=gate_offset,
            gate_limit=gate_limit,
            run_id=run_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
