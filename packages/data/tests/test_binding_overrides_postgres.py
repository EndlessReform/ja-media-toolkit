"""PostgreSQL binding decisions composed over automatic DuckLake output."""

from __future__ import annotations

from datetime import UTC, datetime
import os
import uuid

import psycopg
import pytest
from psycopg import sql

from ja_media_data.storage.binding_overrides import (
    BindingOverrideRepository,
)
from ja_media_data.storage.binding_schema import (
    apply_postgres_schema,
    postgres_url_for_psycopg,
)
from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.products.binding_acceptance.compiler import (
    ACCEPTANCE_POLICY_VERSION,
    compile_product as compile_acceptance_product,
)
from ja_media_data.products.binding_acceptance.repository import replace_product
from ja_media_data.products.lineage import MaterializationCatalog, structural_build_key
from ja_media_data.products.materialization import MaterializationContext
from ja_media_data.products.episode_resolution.models import (
    BindingProposal,
    BindingConflictError,
    CaptureObservation,
    ResolutionBatch,
    ResolutionIssueClaim,
)
from ja_media_data.operator.resolution_review.factory import source_token
from ja_media_data.operator.resolution_review.catalog import (
    list_resolved_series,
    list_review_series,
)
from ja_media_data.operator.resolution_review.models import SeriesResolutionDraft
from ja_media_data.operator.resolution_review.promotion import (
    ResolutionPromotionService,
)


@pytest.fixture
def repository(tmp_path):
    postgres_url = os.environ.get(
        "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
        "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
        "@127.0.0.1:55432/ja_media_lakehouse_test",
    )
    token = uuid.uuid4().hex
    control_schema = "override_test_" + token
    catalog = connect_catalog(
        CatalogConfig(
            postgres_url=postgres_url,
            metadata_schema="override_catalog_" + token,
            data_path=str(tmp_path / "ducklake"),
        )
    )
    control = psycopg.connect(postgres_url_for_psycopg(postgres_url), autocommit=True)
    apply_schema(catalog)
    assert apply_postgres_schema(control, control_schema=control_schema) == [
        "001_binding_overrides.sql",
        "002_override_revisions.sql",
        "003_resolution_decisions.sql",
    ]
    assert apply_postgres_schema(control, control_schema=control_schema) == []
    yield DuckLakeRepository(
        catalog,
        BindingOverrideRepository(control, control_schema=control_schema),
    )
    catalog.close()
    with control.transaction():
        control.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(control_schema))
        )
    control.close()


def index(repository: DuckLakeRepository, *capture_ids: str) -> None:
    observations = [
        CaptureObservation(
            capture_id=capture_id,
            series_namespace="anilist",
            series_id="15451",
            manifest_bucket="bronze",
            manifest_key=f"metadata/{capture_id}.json",
            manifest_etag="etag-1",
            manifest_schema_version=1,
            manifest_modified_at=datetime(2026, 7, 14, tzinfo=UTC),
            observed_at=datetime(2026, 7, 15, tzinfo=UTC),
        )
        for capture_id in capture_ids
    ]
    repository.replace_bronze_captures(observations, "bronze-" + "-".join(capture_ids))


def test_override_and_unbind_are_immediately_effective(repository) -> None:
    assert repository.override_repository.current_revision() == 0
    index(repository, "capture-1", "capture-2")
    repository.replace_resolution_tables(
        ResolutionBatch(
            hints=(),
            issues=(),
            proposals=(
                BindingProposal(
                    proposal_id="automatic-proposal",
                    namespace="anilist",
                    series_id="15451",
                    episode="3",
                    audio_capture_id="capture-1",
                    proposal_method="resolver",
                    proposal_evidence={"source": "test"},
                    input_data_version="etag-1",
                    recipe_version="resolver-v1",
                ),
            ),
        ),
        "automatic-v1",
    )

    override_id = repository.append_override(
        namespace="anilist",
        series_id="15451",
        episode="3",
        audio_capture_id="capture-2",
        decision_note="manual correction",
    )
    current = repository.get_current_binding("anilist", "15451", "3")
    assert current is not None
    assert current.binding_id == override_id
    assert current.audio_capture_id == "capture-2"
    assert repository.get_current_binding_for_capture("capture-1") is None

    repository.append_override(
        namespace="anilist",
        series_id="15451",
        episode="3",
        audio_capture_id=None,
        decision_note="not episode 3",
    )
    assert repository.get_current_binding("anilist", "15451", "3") is None
    assert repository.override_repository.current_revision() == 2
    assert repository.get_current_binding_for_capture("capture-2") is None


def test_postgres_rejects_one_capture_under_two_override_heads(repository) -> None:
    index(repository, "capture-2")
    repository.append_override(
        namespace="anilist",
        series_id="15451",
        episode="2",
        audio_capture_id="capture-2",
    )

    with pytest.raises(BindingConflictError):
        repository.append_override(
            namespace="anilist",
            series_id="15451",
            episode="4",
            audio_capture_id="capture-2",
        )


def test_findings_report_override_whose_capture_vanished(repository) -> None:
    index(repository, "capture-3")
    repository.append_override(
        namespace="anilist",
        series_id="15451",
        episode="3",
        audio_capture_id="capture-3",
    )
    repository.replace_bronze_captures([], "bronze-empty")

    assert [item.finding_type for item in repository.list_consistency_findings()] == [
        "override_missing_capture"
    ]


def test_findings_report_later_auto_collision_with_override(repository) -> None:
    index(repository, "capture-4")
    repository.append_override(
        namespace="anilist",
        series_id="15451",
        episode="3",
        audio_capture_id="capture-4",
    )
    automatic = BindingProposal(
        proposal_id="automatic-collision",
        namespace="anilist",
        series_id="15451",
        episode="4",
        audio_capture_id="capture-4",
        proposal_method="resolver",
        proposal_evidence={"source": "test"},
        input_data_version="etag-2",
        recipe_version="resolver-v1",
    )
    repository.replace_resolution_tables(
        ResolutionBatch(hints=(), proposals=(automatic,), issues=()),
        "automatic-collision-v1",
    )
    heads = MaterializationCatalog(repository.connection).input_heads(
        "episode_resolution"
    )
    product = compile_acceptance_product(repository.connection)
    replace_product(
        repository.connection,
        product,
        MaterializationContext(
            attempt_id="test:acceptance",
            pipeline_run_id="test",
            recipe_revision=ACCEPTANCE_POLICY_VERSION,
            build_key=structural_build_key(
                "accepted_bindings", ACCEPTANCE_POLICY_VERSION, heads
            ),
            input_heads=heads,
        ),
    )

    assert [item.finding_type for item in repository.list_consistency_findings()] == [
        "override_automatic_capture_collision"
    ]


def test_review_batch_promotes_and_reverses_binding_and_disposition(repository) -> None:
    index(repository, "capture-move", "capture-extra")
    repository.replace_resolution_tables(
        ResolutionBatch(
            hints=(),
            proposals=(),
            issues=(
                ResolutionIssueClaim(
                    "issue-move", "capture-move", None, "invalid", {"reason": "test"}
                ),
                ResolutionIssueClaim(
                    "issue-extra", "capture-extra", None, "invalid", {"reason": "test"}
                ),
            ),
        ),
        "review-promotion-v1",
    )
    reviewed = source_token(repository)
    draft = SeriesResolutionDraft.model_validate(
        {
            "current_anilist_id": 15451,
            "summary": "Move one episode and exclude one extra.",
            "decisions": [
                {
                    "decision": "move_to_another_series",
                    "destination_anilist_id": 200,
                    "files": [{"capture_id": "capture-move", "episode": 3}],
                    "rationale": "Wrong season.",
                },
                {
                    "decision": "leave_out_of_episode_index",
                    "capture_ids": ["capture-extra"],
                    "rationale": "Creditless extra.",
                },
            ],
        }
    )
    service = ResolutionPromotionService(repository, environment="dev")

    accepted = service.accept(draft, reviewed)

    assert accepted["durable_writeback"] is True
    assert (
        repository.get_current_binding("anilist", "200", "3").audio_capture_id
        == "capture-move"
    )
    assert service.disposed_capture_ids() == {"capture-extra"}
    assert list_review_series(repository, snapshot_id=reviewed.snapshot_id).total == 0
    assert list_resolved_series(repository, snapshot_id=reviewed.snapshot_id).total == 1
    history = service.history()
    assert history[0].items[0].capture_id == "capture-move"
    assert history[0].items[1].decision == "leave_out_of_episode_index"

    reversed_result = service.reverse(
        accepted["batch_id"], reason="Operator correction"
    )

    assert reversed_result["status"] == "reversed"
    assert repository.get_current_binding("anilist", "200", "3") is None
    assert service.disposed_capture_ids() == set()
    assert (
        list_review_series(repository, snapshot_id=reviewed.snapshot_id)
        .items[0]
        .issue_count
        == 2
    )
    assert list_resolved_series(repository, snapshot_id=reviewed.snapshot_id).total == 0
    assert service.history()[1].reversed_by == reversed_result["batch_id"]
