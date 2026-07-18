"""CLI adapter for operator campaigns and the local web process."""

from __future__ import annotations

import json

from ja_media_data.lakehouse.repository import repository_from_env
from ja_media_data.operator.application import OperatorApplication


def print_campaigns() -> None:
    """Render the checked-in campaign registry as stable JSON."""

    with repository_from_env() as repository:
        campaigns = OperatorApplication(repository).list_campaigns()
    print(json.dumps([item.model_dump(mode="json") for item in campaigns], indent=2))


def print_campaign(campaign_id: str, *, series_id: str | None) -> None:
    """Render one application snapshot without going through HTTP."""

    with repository_from_env() as repository:
        try:
            snapshot = OperatorApplication(repository).get_campaign_snapshot(
                campaign_id, series_id=series_id
            )
        except KeyError as error:
            raise SystemExit(f"unknown campaign: {campaign_id}") from error
    print(json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True))


def print_recipes(*, query: str | None = None) -> None:
    """Render the real recipe registry and bounded durable observations."""

    with repository_from_env() as repository:
        page = OperatorApplication(repository).list_recipes(query=query)
    print(json.dumps(page.model_dump(mode="json"), indent=2, sort_keys=True))


def print_plan(campaign_id: str) -> None:
    """Render the immutable current plan for one checked-in campaign."""

    with repository_from_env() as repository:
        try:
            plan = OperatorApplication(repository).plan_campaign(campaign_id)
        except KeyError as error:
            raise SystemExit(f"unknown campaign: {campaign_id}") from error
    print(json.dumps(plan.model_dump(mode="json"), indent=2, sort_keys=True))


def run_web(*, port: int) -> None:
    """Serve the read-only workbench on loopback only."""

    if port < 1 or port > 65_535:
        raise SystemExit("--port must be between 1 and 65535")
    import uvicorn

    from ja_media_data.operator.http import create_operator_app

    uvicorn.run(
        create_operator_app(),
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
