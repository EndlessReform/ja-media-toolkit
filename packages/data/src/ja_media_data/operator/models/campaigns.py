"""Campaign index and page envelopes."""

from datetime import datetime

from ja_media_data.operator.models.base import OperatorModel
from ja_media_data.operator.models.products import CanonicalizationLens


class CampaignCard(OperatorModel):
    """One revisioned checked-in campaign suitable for the workboard."""

    campaign_id: str
    revision: int
    name: str
    target: str
    scope: str
    description: str


class CampaignSnapshot(OperatorModel):
    """Campaign envelope whose domain lens remains independently pageable."""

    campaign: CampaignCard
    generated_at: datetime
    lens: CanonicalizationLens
