"""SQLAlchemy tables for capture discovery and stable episode identity.

Large media and immutable open snapshots remain in Garage.  These tables are
the indexed, transactional ledger used for incremental point reads and binding
decisions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Base class shared by the ledger schema and Alembic metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class BronzeCapture(Base):
    """Hot, rebuildable index over canonical Garage bronze manifests."""

    __tablename__ = "bronze_captures"
    __table_args__ = (
        UniqueConstraint(
            "manifest_bucket", "manifest_key", name="uq_bronze_manifest_location"
        ),
        Index("ix_bronze_captures_series", "series_namespace", "series_id"),
    )

    capture_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    series_namespace: Mapped[str] = mapped_column(String(32))
    series_id: Mapped[str] = mapped_column(String(80))
    manifest_bucket: Mapped[str] = mapped_column(String(255))
    manifest_key: Mapped[str] = mapped_column(Text)
    manifest_etag: Mapped[str] = mapped_column(String(128))
    manifest_schema_version: Mapped[int] = mapped_column(Integer)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EpisodeHint(Base):
    """Immutable, versioned resolver claim about one capture."""

    __tablename__ = "episode_hints"
    __table_args__ = (
        UniqueConstraint(
            "capture_id",
            "input_data_version",
            "recipe_version",
            "method",
            "candidate_namespace",
            "candidate_series_id",
            "candidate_episode",
            name="uq_episode_hint_recipe_candidate",
        ),
        Index("ix_episode_hints_capture_created", "capture_id", "created_at"),
    )

    hint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    capture_id: Mapped[str] = mapped_column(
        ForeignKey("bronze_captures.capture_id"), nullable=False
    )
    candidate_namespace: Mapped[str] = mapped_column(String(32))
    candidate_series_id: Mapped[str] = mapped_column(String(80))
    candidate_episode: Mapped[str] = mapped_column(String(80))
    method: Mapped[str] = mapped_column(String(80))
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    input_data_version: Mapped[str] = mapped_column(String(160))
    recipe_version: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dagster_run_id: Mapped[str | None] = mapped_column(String(64))


class EpisodeBinding(Base):
    """Immutable accepted or rejected episode-binding decision."""

    __tablename__ = "episode_bindings"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('accepted', 'rejected')", name="decision_value"
        ),
        Index(
            "ix_episode_bindings_locator_created",
            "namespace",
            "series_id",
            "episode",
            "created_at",
        ),
    )

    binding_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(32))
    series_id: Mapped[str] = mapped_column(String(80))
    episode: Mapped[str] = mapped_column(String(80))
    audio_capture_id: Mapped[str] = mapped_column(
        ForeignKey("bronze_captures.capture_id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16))
    decision_method: Mapped[str] = mapped_column(String(80))
    decision_evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    input_data_version: Mapped[str] = mapped_column(String(160))
    recipe_version: Mapped[str] = mapped_column(String(160))
    supersedes_binding_id: Mapped[str | None] = mapped_column(
        ForeignKey("episode_bindings.binding_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    dagster_run_id: Mapped[str | None] = mapped_column(String(64))


class CurrentEpisodeBinding(Base):
    """Transactionally current binding head used by incremental point reads."""

    __tablename__ = "current_episode_bindings"
    __table_args__ = (
        UniqueConstraint("binding_id", name="uq_current_binding_id"),
        UniqueConstraint("audio_capture_id", name="uq_current_audio_capture_id"),
    )

    namespace: Mapped[str] = mapped_column(String(32), primary_key=True)
    series_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    episode: Mapped[str] = mapped_column(String(80), primary_key=True)
    binding_id: Mapped[str] = mapped_column(
        ForeignKey("episode_bindings.binding_id"), nullable=False
    )
    audio_capture_id: Mapped[str] = mapped_column(
        ForeignKey("bronze_captures.capture_id"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EpisodeResolutionIssue(Base):
    """Durable review item for ambiguity, overlap, or invalid evidence."""

    __tablename__ = "episode_resolution_issues"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('conflict', 'overlap', 'ambiguous', 'invalid')",
            name="kind_value",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'ignored')", name="status_value"
        ),
        Index("ix_episode_resolution_issues_status_created", "status", "created_at"),
        Index("ix_episode_resolution_issues_capture", "capture_id"),
    )

    issue_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    capture_id: Mapped[str] = mapped_column(
        ForeignKey("bronze_captures.capture_id"), nullable=False
    )
    hint_id: Mapped[str | None] = mapped_column(ForeignKey("episode_hints.hint_id"))
    kind: Mapped[str] = mapped_column(String(24))
    details: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default="open"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
