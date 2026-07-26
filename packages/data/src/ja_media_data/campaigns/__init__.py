"""Operator-supported Dagster jobs registered through one composition root."""

from .canonicalization import CANONICALIZATION_CAMPAIGN
from .models import CampaignPresentation, OperatorCampaign


CAMPAIGNS = (CANONICALIZATION_CAMPAIGN,)

__all__ = [
    "CAMPAIGNS",
    "CANONICALIZATION_CAMPAIGN",
    "CampaignPresentation",
    "OperatorCampaign",
]
