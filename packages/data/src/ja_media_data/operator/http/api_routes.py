"""Typed read-only JSON routes over the surface-neutral application."""

from fastapi import APIRouter, Depends, HTTPException, Query

from ja_media_data.operator.application import OperatorApplication
from ja_media_data.operator.http.dependencies import get_application
from ja_media_data.operator.models import (
    CampaignCard,
    CampaignSnapshot,
    RunPage,
    RunSummary,
)


router = APIRouter(prefix="/api/operator/v1")


@router.get("/campaigns", response_model=list[CampaignCard])
def list_campaigns(
    application: OperatorApplication = Depends(get_application),
) -> tuple[CampaignCard, ...]:
    return application.list_campaigns()


@router.get("/campaigns/{campaign_id}", response_model=CampaignSnapshot)
def get_campaign(
    campaign_id: str,
    series_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    application: OperatorApplication = Depends(get_application),
) -> CampaignSnapshot:
    return _campaign_snapshot(
        application, campaign_id, series_id=series_id, run_id=run_id
    )


@router.get("/runs", response_model=RunPage)
def list_runs(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    application: OperatorApplication = Depends(get_application),
) -> RunPage:
    return application.list_runs(offset=offset, limit=limit)


@router.get("/runs/{run_id}", response_model=RunSummary)
def get_run(
    run_id: str,
    application: OperatorApplication = Depends(get_application),
) -> RunSummary:
    try:
        return application.get_run(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="run not found") from error


def _campaign_snapshot(
    application: OperatorApplication,
    campaign_id: str,
    *,
    series_id: str | None,
    run_id: str | None,
) -> CampaignSnapshot:
    try:
        return application.get_campaign_snapshot(
            campaign_id, series_id=series_id, run_id=run_id
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="campaign not found") from error
