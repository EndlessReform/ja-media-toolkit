"""Small read model for paging reviewable AniList series."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.lakehouse.time_travel import table_ref
from ja_media_data.operator.resolution_review.history import ResolutionDecisionHistory


@dataclass(frozen=True)
class ReviewSeries:
    """One Bronze-declared AniList series with rejected captures."""

    anilist_id: int
    title: str
    issue_count: int


@dataclass(frozen=True)
class ReviewSeriesPage:
    """One bounded rail page."""

    items: tuple[ReviewSeries, ...]
    total: int
    offset: int
    limit: int


def list_review_series(
    repository: DuckLakeRepository,
    *,
    snapshot_id: int,
    offset: int = 0,
    limit: int = 50,
) -> ReviewSeriesPage:
    """Group current resolver issues by their Bronze-declared AniList ID."""

    if offset < 0 or not 1 <= limit <= 50:
        raise ValueError("series pages require offset >= 0 and limit 1..50")
    issues = table_ref("resolution_issues_auto", snapshot_id=snapshot_id, alias="issue")
    captures = table_ref("bronze_captures", snapshot_id=snapshot_id, alias="capture")
    disposed = ResolutionDecisionHistory(repository).resolved_capture_ids()
    exclusions = ""
    exclusion_params: list[object] = []
    if disposed:
        exclusions = (
            " AND issue.capture_id NOT IN (" + ",".join("?" for _ in disposed) + ")"
        )
        exclusion_params = sorted(disposed)
    base = (
        f"FROM {issues} JOIN {captures} ON capture.capture_id = issue.capture_id "
        "WHERE capture.series_namespace = 'anilist' "
        "AND try_cast(capture.series_id AS BIGINT) IS NOT NULL" + exclusions
    )
    total = repository.connection.execute(
        f"SELECT count(DISTINCT capture.series_id) {base}", exclusion_params
    ).fetchone()[0]
    rows = repository.connection.execute(
        f"""SELECT capture.series_id, count(*), min(issue.details)
              {base}
             GROUP BY capture.series_id
             ORDER BY try_cast(capture.series_id AS BIGINT), capture.series_id
             LIMIT ? OFFSET ?""",
        [*exclusion_params, limit, offset],
    ).fetchall()
    items = tuple(
        ReviewSeries(
            anilist_id=int(row[0]),
            title=_series_title(row[2], int(row[0])),
            issue_count=int(row[1]),
        )
        for row in rows
    )
    return ReviewSeriesPage(items=items, total=int(total), offset=offset, limit=limit)


def list_resolved_series(
    repository: DuckLakeRepository,
    *,
    snapshot_id: int,
    offset: int = 0,
    limit: int = 50,
) -> ReviewSeriesPage:
    """Page source series with active, human-approved resolution batches."""

    if offset < 0 or not 1 <= limit <= 50:
        raise ValueError("series pages require offset >= 0 and limit 1..50")
    summaries, total = ResolutionDecisionHistory(repository).resolved_series(
        offset=offset, limit=limit
    )
    items = tuple(
        ReviewSeries(
            anilist_id=item.anilist_id,
            title=_title_for_series(repository, snapshot_id, item.anilist_id),
            issue_count=item.decision_count,
        )
        for item in summaries
    )
    return ReviewSeriesPage(items=items, total=total, offset=offset, limit=limit)


def _series_title(details: object, anilist_id: int) -> str:
    payload = json.loads(details) if isinstance(details, str) else dict(details)
    titles = payload.get("metadata", {}).get("titles", [])
    return str(titles[0]) if titles else f"AniList {anilist_id}"


def _title_for_series(
    repository: DuckLakeRepository, snapshot_id: int, anilist_id: int
) -> str:
    issues = table_ref("resolution_issues_auto", snapshot_id=snapshot_id, alias="issue")
    captures = table_ref("bronze_captures", snapshot_id=snapshot_id, alias="capture")
    row = repository.connection.execute(
        f"""SELECT min(issue.details) FROM {issues} JOIN {captures}
               ON capture.capture_id = issue.capture_id
             WHERE capture.series_namespace = 'anilist' AND capture.series_id = ?""",
        [str(anilist_id)],
    ).fetchone()
    return (
        _series_title(row[0], anilist_id) if row and row[0] else f"AniList {anilist_id}"
    )
