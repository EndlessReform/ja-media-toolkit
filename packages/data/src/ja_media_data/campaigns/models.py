"""Structural binding between an executable Dagster job and its operator lens."""

from __future__ import annotations

from dataclasses import dataclass
import re

import dagster as dg


_CAMPAIGN_ID = re.compile(r"^[a-z][a-z0-9-]+$")


@dataclass(frozen=True)
class CampaignPresentation:
    """Human semantics and lens requirements for one executable campaign.

    ``conclusion_assets`` are not a second execution selection. They declare
    what the lens promises to show; startup verifies that the actual Dagster
    job selects them.
    """

    label: str
    description: str
    scope_kind: str
    lens_kind: str
    conclusion_assets: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.conclusion_assets:
            raise ValueError("campaign lens must declare a conclusion asset")


@dataclass(frozen=True)
class OperatorCampaign:
    """An actual Dagster job coupled to the operator surface that explains it."""

    campaign_id: str
    revision: int
    job: dg.UnresolvedAssetJobDefinition
    presentation: CampaignPresentation

    @classmethod
    def create(
        cls,
        *,
        campaign_id: str,
        revision: int,
        job_name: str,
        selection: dg.AssetSelection,
        presentation: CampaignPresentation,
    ) -> OperatorCampaign:
        """Create the job and its identifying tags as one indivisible object."""

        if revision < 1:
            raise ValueError("campaign revision must be positive")
        if not _CAMPAIGN_ID.fullmatch(campaign_id):
            raise ValueError("campaign ID must be lowercase kebab-case")
        job = dg.define_asset_job(
            name=job_name,
            selection=selection,
            executor_def=dg.in_process_executor,
            tags={
                "ja_media/campaign": campaign_id,
                "ja_media/campaign_revision": str(revision),
                "ja_media/scope": presentation.scope_kind,
            },
        )
        return cls(
            campaign_id=campaign_id,
            revision=revision,
            job=job,
            presentation=presentation,
        )
