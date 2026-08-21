"""Construction shared by HTTP, integration harnesses, and a future TUI."""

from __future__ import annotations

from ja_media_core.anilist_search import AniListSearchClient
from ja_media_core.kitsunekko import KitsunekkoSubtitlesClient

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.resolution_review.drafts import DraftApplier
from ja_media_data.operator.resolution_review.state import SourceToken
from ja_media_data.operator.resolution_review.toolbox import (
    RepositoryFactory,
    ResolutionToolbox,
    ReviewContext,
)
from ja_media_data.products.canonical_inputs.compiler import binding_override_revision
from ja_media_data.products.lineage import MaterializationCatalog
from ja_media_data.storage.bronze import BronzeStore


def source_token(repository: DuckLakeRepository) -> SourceToken:
    """Pin a review to one durable resolution product and control revision."""

    head = MaterializationCatalog(repository.connection).current_head(
        "episode_resolution"
    )
    if head is None or head.snapshot_id is None:
        raise RuntimeError("episode_resolution has no durable corpus materialization")
    return SourceToken(
        resolution_materialization_id=head.materialization_id,
        snapshot_id=head.snapshot_id,
        binding_revision=binding_override_revision(repository),
        disposition_revision=(
            repository.override_repository.control_revision("capture_dispositions")
            if repository.override_repository is not None
            and hasattr(repository.override_repository, "control_revision")
            else 0
        ),
    )


def make_review_context(
    current_anilist_id: int,
    *,
    repositories: RepositoryFactory,
    bronze: BronzeStore,
    anilist: AniListSearchClient,
    kitsunekko: KitsunekkoSubtitlesClient | None = None,
    apply_draft: DraftApplier | None = None,
) -> ReviewContext:
    """Build one context without retaining a checked-out database connection."""

    if current_anilist_id < 1:
        raise ValueError("current AniList ID must be positive")
    with repositories() as repository:
        token = source_token(repository)
    return ReviewContext(
        current_anilist_id=current_anilist_id,
        toolbox=ResolutionToolbox(
            repositories=repositories,
            bronze=bronze,
            anilist=anilist,
            kitsunekko=kitsunekko,
            source_token=token,
            apply_draft=apply_draft,
        ),
    )
