"""Batch replacement and fingerprint tests for automatic identity products."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
import uuid

import pytest

from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.products.episode_resolution.models import (
    BindingProposal,
    CaptureObservation,
    HintClaim,
    ResolutionBatch,
    ResolutionIssueClaim,
)


@pytest.fixture
def catalog(tmp_path):
    postgres_url = os.environ.get(
        "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
        "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
        "@127.0.0.1:55432/ja_media_lakehouse_test",
    )
    connection = connect_catalog(
        CatalogConfig(
            postgres_url=postgres_url,
            metadata_schema="phase_c2_repository_" + uuid.uuid4().hex,
            data_path=str(tmp_path / "ducklake"),
        )
    )
    apply_schema(connection)
    yield connection
    connection.close()


def observation(
    capture_id: str, *, minute: int = 0, key: str | None = None
) -> CaptureObservation:
    return CaptureObservation(
        capture_id=capture_id,
        series_namespace="anilist",
        series_id="15451",
        manifest_bucket="bronze",
        manifest_key=key or f"metadata/{capture_id}.json",
        manifest_etag=f"etag-{minute}",
        manifest_schema_version=1,
        manifest_modified_at=datetime(2026, 7, 15, 11, minute, tzinfo=UTC),
        observed_at=datetime(2026, 7, 15, 12, minute, tzinfo=UTC),
    )


def batch(capture_id: str, episode: str) -> ResolutionBatch:
    hint = HintClaim(
        hint_id=f"hint-{capture_id}-{episode}",
        capture_id=capture_id,
        candidate_namespace="anilist",
        candidate_series_id="15451",
        candidate_episode=episode,
        method="test",
        confidence=1.0,
        evidence={"episode": episode},
        input_data_version="etag-0",
        recipe_version="resolver-v1",
    )
    binding = BindingProposal(
        proposal_id=f"proposal-{capture_id}-{episode}",
        namespace="anilist",
        series_id="15451",
        episode=episode,
        audio_capture_id=capture_id,
        proposal_method="test",
        proposal_evidence={"hint_id": hint.hint_id},
        input_data_version="etag-0",
        recipe_version="resolver-v1",
    )
    return ResolutionBatch(hints=(hint,), proposals=(binding,), issues=())


def test_bronze_rescan_replaces_and_identical_fingerprint_is_noop(catalog) -> None:
    repository = DuckLakeRepository(catalog)
    first = repository.replace_bronze_captures(
        [observation("capture-1")], "bronze-fingerprint"
    )
    second = repository.replace_bronze_captures(
        [observation("capture-1", minute=5)], "bronze-fingerprint"
    )

    assert first.written is True
    assert second.written is False
    times = catalog.execute(
        "SELECT first_observed_at, last_observed_at FROM bronze_captures"
    ).fetchone()
    assert times[1] - times[0] == timedelta(0)


def test_capture_move_is_a_read_side_finding(catalog) -> None:
    repository = DuckLakeRepository(catalog)
    repository.replace_bronze_captures(
        [
            observation("capture-1", key="first.json"),
            observation("capture-1", key="moved.json"),
        ],
        "moved-fingerprint",
    )

    findings = repository.list_consistency_findings()
    assert [item.finding_type for item in findings] == [
        "bronze_capture_identity_collision"
    ]


def test_resolution_replacement_noop_and_time_travel(catalog) -> None:
    repository = DuckLakeRepository(catalog)
    repository.replace_bronze_captures(
        [observation("capture-1")], "bronze-fingerprint"
    )
    first = repository.replace_resolution_tables(batch("capture-1", "3"), "v1")
    snapshot = catalog.execute("FROM lakehouse.current_snapshot()").fetchone()[0]
    repeated = repository.replace_resolution_tables(
        batch("capture-1", "3"), "v1"
    )
    changed = repository.replace_resolution_tables(batch("capture-1", "4"), "v2")

    assert first.written is True
    assert repeated.written is False
    assert changed.written is True
    assert catalog.execute(
        "SELECT episode FROM episode_binding_proposals"
    ).fetchone()[0] == "4"
    assert catalog.execute(
        f"SELECT episode FROM lakehouse.episode_binding_proposals "
        f"AT (VERSION => {snapshot})"
    ).fetchone()[0] == "3"


def test_batch_writer_allows_duplicate_proposed_locators(catalog) -> None:
    repository = DuckLakeRepository(catalog)
    first = batch("capture-1", "3").proposals[0]
    second = batch("capture-2", "3").proposals[0]
    invalid = ResolutionBatch(hints=(), proposals=(first, second), issues=())

    repository.replace_resolution_tables(invalid, "valid-proposals")
    assert catalog.execute(
        "SELECT count(*) FROM episode_binding_proposals"
    ).fetchone()[0] == 2


def test_derived_issue_rows_are_replaced_not_resolved(catalog) -> None:
    repository = DuckLakeRepository(catalog)
    issue = ResolutionIssueClaim(
        issue_id="issue-1",
        capture_id="capture-1",
        hint_id=None,
        kind="ambiguous",
        details={"reason": "test"},
    )
    repository.replace_resolution_tables(
        ResolutionBatch(hints=(), proposals=(), issues=(issue,)), "issue-v1"
    )
    repository.replace_resolution_tables(batch("capture-1", "3"), "issue-v2")

    assert catalog.execute("SELECT count(*) FROM resolution_issues_auto").fetchone()[0] == 0
