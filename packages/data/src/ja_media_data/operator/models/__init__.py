"""Stable operator DTOs grouped by the surface they describe."""

from .campaigns import CampaignCard, CampaignSnapshot
from .products import (
    AcceptanceObservation,
    AudioEligibilityObservation,
    CampaignProgress,
    CandidateObservation,
    CanonicalInputObservation,
    CanonicalizationGate,
    CanonicalizationLens,
    ResolutionIssueObservation,
    StageObservation,
    StageResultPage,
)
from .runs import RunPage, RunStepSummary, RunSummary

__all__ = [
    "AcceptanceObservation",
    "AudioEligibilityObservation",
    "CampaignCard",
    "CampaignProgress",
    "CampaignSnapshot",
    "CandidateObservation",
    "CanonicalInputObservation",
    "CanonicalizationGate",
    "CanonicalizationLens",
    "ResolutionIssueObservation",
    "RunPage",
    "RunStepSummary",
    "RunSummary",
    "StageObservation",
    "StageResultPage",
]
