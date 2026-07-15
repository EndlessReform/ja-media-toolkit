"""Create the episode-identity ledger.

Revision ID: 20260713_0001
Revises:
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260713_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create only new domain tables, constraints, and indexes."""

    op.create_table(
        "bronze_captures",
        sa.Column("capture_id", sa.String(length=80), nullable=False),
        sa.Column("series_namespace", sa.String(length=32), nullable=False),
        sa.Column("series_id", sa.String(length=80), nullable=False),
        sa.Column("manifest_bucket", sa.String(length=255), nullable=False),
        sa.Column("manifest_key", sa.Text(), nullable=False),
        sa.Column("manifest_etag", sa.String(length=128), nullable=False),
        sa.Column("manifest_schema_version", sa.Integer(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("capture_id", name="pk_bronze_captures"),
        sa.UniqueConstraint(
            "manifest_bucket", "manifest_key", name="uq_bronze_manifest_location"
        ),
    )
    op.create_index(
        "ix_bronze_captures_series",
        "bronze_captures",
        ["series_namespace", "series_id"],
    )

    op.create_table(
        "episode_hints",
        sa.Column("hint_id", sa.String(length=80), nullable=False),
        sa.Column("capture_id", sa.String(length=80), nullable=False),
        sa.Column("candidate_namespace", sa.String(length=32), nullable=False),
        sa.Column("candidate_series_id", sa.String(length=80), nullable=False),
        sa.Column("candidate_episode", sa.String(length=80), nullable=False),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_data_version", sa.String(length=160), nullable=False),
        sa.Column("recipe_version", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dagster_run_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["bronze_captures.capture_id"],
            name="fk_episode_hints_capture_id_bronze_captures",
        ),
        sa.PrimaryKeyConstraint("hint_id", name="pk_episode_hints"),
        sa.UniqueConstraint(
            "capture_id",
            "input_data_version",
            "recipe_version",
            "method",
            "candidate_namespace",
            "candidate_series_id",
            "candidate_episode",
            name="uq_episode_hint_recipe_candidate",
        ),
    )
    op.create_index(
        "ix_episode_hints_capture_created",
        "episode_hints",
        ["capture_id", "created_at"],
    )

    op.create_table(
        "episode_bindings",
        sa.Column("binding_id", sa.String(length=80), nullable=False),
        sa.Column("namespace", sa.String(length=32), nullable=False),
        sa.Column("series_id", sa.String(length=80), nullable=False),
        sa.Column("episode", sa.String(length=80), nullable=False),
        sa.Column("audio_capture_id", sa.String(length=80), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decision_method", sa.String(length=80), nullable=False),
        sa.Column(
            "decision_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("input_data_version", sa.String(length=160), nullable=False),
        sa.Column("recipe_version", sa.String(length=160), nullable=False),
        sa.Column("supersedes_binding_id", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dagster_run_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected')",
            name="decision_value",
        ),
        sa.ForeignKeyConstraint(
            ["audio_capture_id"],
            ["bronze_captures.capture_id"],
            name="fk_episode_bindings_audio_capture_id_bronze_captures",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_binding_id"],
            ["episode_bindings.binding_id"],
            name="fk_episode_bindings_supersedes_binding_id_episode_bindings",
        ),
        sa.PrimaryKeyConstraint("binding_id", name="pk_episode_bindings"),
    )
    op.create_index(
        "ix_episode_bindings_locator_created",
        "episode_bindings",
        ["namespace", "series_id", "episode", "created_at"],
    )

    op.create_table(
        "current_episode_bindings",
        sa.Column("namespace", sa.String(length=32), nullable=False),
        sa.Column("series_id", sa.String(length=80), nullable=False),
        sa.Column("episode", sa.String(length=80), nullable=False),
        sa.Column("binding_id", sa.String(length=80), nullable=False),
        sa.Column("audio_capture_id", sa.String(length=80), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["audio_capture_id"],
            ["bronze_captures.capture_id"],
            name="fk_current_episode_bindings_audio_capture_id_bronze_captures",
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"],
            ["episode_bindings.binding_id"],
            name="fk_current_episode_bindings_binding_id_episode_bindings",
        ),
        sa.PrimaryKeyConstraint(
            "namespace",
            "series_id",
            "episode",
            name="pk_current_episode_bindings",
        ),
        sa.UniqueConstraint("audio_capture_id", name="uq_current_audio_capture_id"),
        sa.UniqueConstraint("binding_id", name="uq_current_binding_id"),
    )

    op.create_table(
        "episode_resolution_issues",
        sa.Column("issue_id", sa.String(length=80), nullable=False),
        sa.Column("capture_id", sa.String(length=80), nullable=False),
        sa.Column("hint_id", sa.String(length=80), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('conflict', 'overlap', 'ambiguous', 'invalid')",
            name="kind_value",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'ignored')",
            name="status_value",
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["bronze_captures.capture_id"],
            name="fk_episode_resolution_issues_capture_id_bronze_captures",
        ),
        sa.ForeignKeyConstraint(
            ["hint_id"],
            ["episode_hints.hint_id"],
            name="fk_episode_resolution_issues_hint_id_episode_hints",
        ),
        sa.PrimaryKeyConstraint("issue_id", name="pk_episode_resolution_issues"),
    )
    op.create_index(
        "ix_episode_resolution_issues_capture",
        "episode_resolution_issues",
        ["capture_id"],
    )
    op.create_index(
        "ix_episode_resolution_issues_status_created",
        "episode_resolution_issues",
        ["status", "created_at"],
    )


def downgrade() -> None:
    """Refuse destructive rollback; restore a reviewed backup instead."""

    raise RuntimeError("destructive downgrade is intentionally unsupported")
