"""FastAPI-lifetime ownership for attached repositories and projection cache."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from queue import LifoQueue
import secrets
from typing import Iterator

from ja_media_core.anilist_search import HttpAniListSearchClient
from ja_media_core.kitsunekko import HttpKitsunekkoSubtitlesClient

from ja_media_data.lakehouse.repository import (
    DuckLakeRepository,
    repository_from_settings,
)
from ja_media_data.operator.application import OperatorApplication
from ja_media_data.operator.cache import ProjectionCache
from ja_media_data.operator.campaigns import CampaignCatalog
from ja_media_data.operator.resolution_review.state import PausedReview
from ja_media_data.operator.resolution_review.state import expire_paused_reviews
from ja_media_data.operator.resolution_review.agent import make_resolution_agent
from ja_media_data.operator.resolution_review.catalog import (
    ReviewSeriesPage,
    list_resolved_series,
    list_review_series,
)
from ja_media_data.operator.resolution_review.factory import (
    make_review_context,
    source_token,
)
from ja_media_data.operator.resolution_review.provider import (
    ModelChoice,
    make_run_config,
)
from ja_media_data.operator.resolution_review.history import (
    DecisionBatch,
    ResolutionDecisionHistory,
)
from ja_media_data.operator.resolution_review.promotion import (
    ResolutionPromotionService,
)
from ja_media_data.operator.resolution_review.streaming import ResolutionReviewSession
from ja_media_data.operator.resolution_review.toolbox import ReviewContext
from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.gateway import DagsterGateway
from ja_media_data.settings import get_settings
from ja_media_data.storage.bronze import bronze_store_from_settings


class RepositoryPool:
    """A tiny pool of fully attached DuckLake/PostgreSQL repository clients."""

    def __init__(self, size: int = 2) -> None:
        if size < 1:
            raise ValueError("repository pool size must be positive")
        self._stack = ExitStack()
        self._available: LifoQueue[DuckLakeRepository] = LifoQueue(maxsize=size)
        try:
            for _ in range(size):
                repository = self._stack.enter_context(
                    repository_from_settings(ensure_schema=False)
                )
                self._available.put(repository)
        except Exception:
            self._stack.close()
            raise

    @contextmanager
    def borrow(self) -> Iterator[DuckLakeRepository]:
        """Give one request exclusive use of one non-thread-safe connection."""

        repository = self._available.get()
        try:
            yield repository
        finally:
            self._available.put(repository)

    def close(self) -> None:
        self._stack.close()


class OperatorRuntime:
    """Shared process state created and destroyed by FastAPI lifespan."""

    def __init__(self, *, pool_size: int = 2, cache_entries: int = 512) -> None:
        self.pool = RepositoryPool(pool_size)
        self.cache = ProjectionCache(cache_entries)
        self.gateway = DagsterGateway.from_settings()
        self.campaigns = CampaignCatalog(build_definitions())
        # HITL state is intentionally process-local and owned by the app lifespan.
        self.paused_reviews: dict[str, PausedReview] = {}

    @contextmanager
    def application(self) -> Iterator[OperatorApplication]:
        """Create a cheap request application over one borrowed repository."""

        with self.pool.borrow() as repository:
            yield OperatorApplication(
                repository, self.gateway, campaigns=self.campaigns, cache=self.cache
            )

    def review_context(
        self, current_anilist_id: int, *, apply_draft=None
    ) -> ReviewContext:
        """Create a review context without checking out a repository for its lifetime."""

        settings = get_settings()
        return make_review_context(
            current_anilist_id,
            repositories=self.pool.borrow,
            bronze=bronze_store_from_settings(settings),
            anilist=HttpAniListSearchClient(
                f"{settings.services.root_url.rstrip('/')}/api/v1/anilist"
            ),
            kitsunekko=HttpKitsunekkoSubtitlesClient(
                f"{settings.services.root_url.rstrip('/')}/api/v1/subtitles"
            ),
            apply_draft=apply_draft,
        )

    def review_series(
        self, *, view: str = "pending", offset: int = 0, limit: int = 50
    ) -> ReviewSeriesPage:
        """Return the current bounded series rail."""

        with self.pool.borrow() as repository:
            token = source_token(repository)
            lister = list_resolved_series if view == "resolved" else list_review_series
            return lister(
                repository, snapshot_id=token.snapshot_id, offset=offset, limit=limit
            )

    def active_resolutions(self, anilist_id: int) -> tuple[DecisionBatch, ...]:
        """Return the reversible crosswalk batches for one source series."""

        with self.pool.borrow() as repository:
            return ResolutionDecisionHistory(repository).active_for_series(anilist_id)

    def start_review(
        self, current_anilist_id: int, choice: ModelChoice
    ) -> ResolutionReviewSession:
        """Build one review; only local mode substitutes a non-durable accept."""

        settings = get_settings()
        apply_draft = _accept_local_preview
        if settings.environment != "local":
            apply_draft = self._promote_draft
        context = self.review_context(current_anilist_id, apply_draft=apply_draft)
        return ResolutionReviewSession(
            agent=make_resolution_agent(),
            context=context,
            run_config=make_run_config(settings.agent, choice),
            max_turns=_max_turns(choice.max_turns, default=settings.agent.max_turns),
        )

    @property
    def review_mode(self) -> str:
        """Expose the configured write behavior to the server-rendered UI."""

        return get_settings().environment

    def resolution_history(self, *, limit: int = 50) -> tuple[DecisionBatch, ...]:
        """Return recent durable accept/reverse batches."""

        with self.pool.borrow() as repository:
            return ResolutionPromotionService(
                repository, environment=get_settings().environment
            ).history(limit=limit)

    def reverse_resolution(self, batch_id: str, *, reason: str) -> dict[str, object]:
        """Reverse one unmodified accepted batch without invoking a model."""

        if get_settings().environment == "local":
            raise RuntimeError("local preview mode has no durable decisions to reverse")
        with self.pool.borrow() as repository:
            return ResolutionPromotionService(
                repository, environment=get_settings().environment
            ).reverse(batch_id, reason=reason)

    def _promote_draft(self, draft, reviewed) -> dict[str, object]:
        with self.pool.borrow() as repository:
            return ResolutionPromotionService(
                repository, environment=get_settings().environment
            ).accept(draft, reviewed)

    def pause_review(self, session: ResolutionReviewSession) -> str:
        """Put one paused session in the lifespan-owned dictionary."""

        settings = get_settings().agent
        expire_paused_reviews(
            self.paused_reviews, ttl_seconds=settings.paused_review_ttl_seconds
        )
        if len(self.paused_reviews) >= settings.paused_review_limit:
            raise RuntimeError("too many reviews are waiting for approval")
        token = secrets.token_urlsafe(24)
        self.paused_reviews[token] = PausedReview(
            session=session, created_at=datetime.now(UTC)
        )
        return token

    def take_paused_review(self, token: str) -> ResolutionReviewSession:
        """Consume one opaque single-use browser token."""

        settings = get_settings().agent
        expire_paused_reviews(
            self.paused_reviews, ttl_seconds=settings.paused_review_ttl_seconds
        )
        try:
            return self.paused_reviews.pop(token).session
        except KeyError as error:
            raise KeyError("review approval expired or was already used") from error

    def close(self) -> None:
        self.paused_reviews.clear()
        self.cache.clear()
        self.gateway.close()
        self.pool.close()


def _accept_local_preview(draft, _source_token) -> dict:
    """Finish the localhost lifecycle without pretending to write control rows."""

    return {
        "status": "accepted_local_preview",
        "decisions": len(draft.decisions),
        "durable_writeback": False,
    }


def _max_turns(requested: int | None, *, default: int) -> int:
    """Validate one request override against the same small server bound."""

    value = requested if requested is not None else default
    if not 1 <= value <= 30:
        raise ValueError("max turns must be between 1 and 30")
    return value
