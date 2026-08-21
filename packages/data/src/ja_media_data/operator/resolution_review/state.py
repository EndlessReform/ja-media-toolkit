"""Ephemeral state for a draft and an SDK run paused at human approval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ja_media_data.operator.resolution_review.streaming import ResolutionReviewSession


@dataclass(frozen=True)
class SourceToken:
    """Exact automatic product and control revision reviewed by the agent."""

    resolution_materialization_id: str
    snapshot_id: int
    binding_revision: int
    disposition_revision: int


@dataclass
class PausedReview:
    """One passive in-memory session waiting for a later browser decision."""

    session: ResolutionReviewSession
    created_at: datetime


def expire_paused_reviews(
    paused_reviews: dict[str, PausedReview],
    *,
    ttl_seconds: int,
    now: datetime | None = None,
) -> int:
    """Remove expired entries from the application-lifetime plain dictionary."""

    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=ttl_seconds)
    expired = [
        token for token, review in paused_reviews.items() if review.created_at < cutoff
    ]
    for token in expired:
        del paused_reviews[token]
    return len(expired)
