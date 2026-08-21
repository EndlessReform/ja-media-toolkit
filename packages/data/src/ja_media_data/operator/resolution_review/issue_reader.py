"""Snapshot-pinned rejected-capture reads shared by the agent toolbox."""

from __future__ import annotations

import json

from ja_media_data.lakehouse.time_travel import table_ref
from ja_media_data.operator.resolution_review.history import ResolutionDecisionHistory


def read_issue_rows(repositories, source_token, where, params, *, limit, offset):
    """Read one bounded page while masking approved capture dispositions."""

    issues = table_ref(
        "resolution_issues_auto", snapshot_id=source_token.snapshot_id, alias="issue"
    )
    captures = table_ref(
        "bronze_captures", snapshot_id=source_token.snapshot_id, alias="capture"
    )
    with repositories() as repository:
        disposed = ResolutionDecisionHistory(repository).resolved_capture_ids()
        if disposed:
            where += (
                " AND issue.capture_id NOT IN (" + ",".join("?" for _ in disposed) + ")"
            )
            params = [*params, *sorted(disposed)]
        rows = repository.connection.execute(
            f"""SELECT issue.issue_id, issue.capture_id, issue.hint_id, issue.kind,
                       issue.details, capture.series_namespace, capture.series_id,
                       capture.manifest_bucket, capture.manifest_key,
                       capture.manifest_etag
                  FROM {issues} JOIN {captures}
                    ON capture.capture_id = issue.capture_id
                 WHERE {where}
                 ORDER BY capture.manifest_key, issue.issue_id LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
    return [_issue_dict(row) for row in rows]


def _issue_dict(row: tuple) -> dict:
    details = json.loads(row[4]) if isinstance(row[4], str) else dict(row[4])
    return {
        "issue_id": str(row[0]),
        "capture_id": str(row[1]),
        "hint_id": str(row[2]) if row[2] else None,
        "kind": str(row[3]),
        "details": details,
        "reason": details.get("reason"),
        "stem": details.get("stem"),
        "source_hint": details.get("source_hint") or details.get("stem") or str(row[8]),
        "series_namespace": str(row[5]),
        "series_id": str(row[6]),
        "manifest_bucket": str(row[7]),
        "manifest_key": str(row[8]),
        "manifest_etag": str(row[9]),
    }
