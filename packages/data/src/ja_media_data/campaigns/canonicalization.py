"""Canonicalization campaign: binding proposals through canonical inputs."""

from __future__ import annotations

import dagster as dg

from .models import CampaignPresentation, OperatorCampaign


_CONCLUSION_ASSETS = (
    "canonical_episode_inputs",
    "canonical_subtitle_inputs",
)

CANONICALIZATION_CAMPAIGN = OperatorCampaign.create(
    campaign_id="canonicalization-gate",
    revision=1,
    job_name="canonicalization_campaign",
    selection=dg.AssetSelection.assets(*_CONCLUSION_ASSETS).upstream(
        include_self=True
    ).required_multi_asset_neighbors(),
    presentation=CampaignPresentation(
        label="Binding & canonicalization desk",
        description=(
            "Inspect resolver proposals, automatic admission, overrides, "
            "and canonical captures."
        ),
        scope_kind="corpus",
        lens_kind="canonicalization",
        conclusion_assets=_CONCLUSION_ASSETS,
    ),
)
