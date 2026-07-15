"""Read-only projections for understanding resolver failures outside Dagster."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ja_media_data.models import (
    BronzeCapture,
    CurrentEpisodeBinding,
    EpisodeHint,
    EpisodeResolutionIssue,
)


@dataclass(frozen=True)
class IssueDiagnostic:
    """Compact failure row with the evidence people actually inspect first."""

    capture_id: str
    series_id: str
    kind: str
    reason: str
    stem: str | None
    ptn_episode: int | None
    explicit_tokens: list[int]
    details: dict[str, Any]


class ResolutionDiagnostics:
    """Bounded ledger reports for the local developer CLI."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def summary(self) -> dict[str, int]:
        """Count captures, hints, bindings, and open review items."""

        with self._sessions() as session:
            return {
                "captures": _count(session, BronzeCapture),
                "hints": _count(session, EpisodeHint),
                "current_bindings": _count(session, CurrentEpisodeBinding),
                "open_issues": session.scalar(
                    select(func.count())
                    .select_from(EpisodeResolutionIssue)
                    .where(EpisodeResolutionIssue.status == "open")
                )
                or 0,
            }

    def open_issues(self, *, limit: int = 100) -> list[IssueDiagnostic]:
        """Return newest open issues with parser evidence expanded."""

        with self._sessions() as session:
            rows = session.execute(
                select(EpisodeResolutionIssue, BronzeCapture.series_id)
                .join(
                    BronzeCapture,
                    BronzeCapture.capture_id == EpisodeResolutionIssue.capture_id,
                )
                .where(EpisodeResolutionIssue.status == "open")
                .order_by(EpisodeResolutionIssue.created_at.desc())
                .limit(limit)
            )
            results = []
            for issue, series_id in rows:
                details = issue.details
                results.append(
                    IssueDiagnostic(
                        capture_id=issue.capture_id,
                        series_id=series_id,
                        kind=issue.kind,
                        reason=str(details.get("reason", "unknown")),
                        stem=_optional_text(details.get("stem")),
                        ptn_episode=_optional_int(
                            details.get("ptn_ordinary_episode")
                        ),
                        explicit_tokens=[
                            int(value)
                            for value in details.get("explicit_episode_tokens", [])
                            if isinstance(value, int)
                        ],
                        details=details,
                    )
                )
            return results


def _count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
