"""End-to-end tests for the first operator campaign and HTTP adapter."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest

from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.application import OperatorApplication
from ja_media_data.operator.cache import ProjectionCache
from ja_media_data.operator.http.app import create_operator_app
from ja_media_data.operator.http.dependencies import get_application
from ja_media_data.operator.http.html_routes import templates
from ja_media_data.products.episode_resolution.models import (
    CaptureObservation,
    ResolutionBatch,
    ResolutionIssueClaim,
)
from operator_test_support import NoopOperatorRuntime, compile_campaign, empty_gateway


@pytest.fixture
def repository(tmp_path):
    postgres_url = os.environ.get(
        "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
        "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
        "@127.0.0.1:55432/ja_media_lakehouse_test",
    )
    connection = connect_catalog(
        CatalogConfig(
            postgres_url=postgres_url,
            metadata_schema="operator_" + uuid.uuid4().hex,
            data_path=str(tmp_path / "ducklake"),
        )
    )
    apply_schema(connection)
    yield DuckLakeRepository(connection)
    connection.close()


def test_campaign_explains_latest_capture_and_stage_runs(repository) -> None:
    campaign = compile_campaign(repository)

    snapshot = OperatorApplication(repository, campaign.gateway).get_campaign_snapshot(
        "canonicalization-gate"
    )

    assert snapshot.lens.progress.locators == 1
    assert snapshot.lens.progress.canonicalized == 1
    gate = snapshot.lens.gates[0]
    assert gate.locator == "anilist:15451:3"
    assert gate.selected_capture_id == "capture-new"
    assert gate.binding_source == "automatic"
    assert "Latest admitted" in gate.selection_reason
    assert gate.candidates == ()
    candidates = OperatorApplication(repository, campaign.gateway).get_candidates(
        "canonicalization-gate", ("anilist", "15451", "3")
    )
    assert [item.capture_id for item in candidates] == [
        "capture-new",
        "capture-old",
    ]
    assert candidates[0].selected is True
    assert {stage.status for stage in snapshot.lens.stages} == {"materialized"}
    canonical = next(
        stage for stage in snapshot.lens.stages if stage.stage == "canonical_inputs"
    )
    assert (canonical.input_rows, canonical.output_rows) == (2, 1)
    assert canonical.latest_run_status == "succeeded"
    assert canonical.latest_run_duration_ms is not None


def test_application_keeps_an_empty_shared_projection_cache(repository) -> None:
    """An empty lifespan cache must not be replaced merely because it is falsey."""

    cache = ProjectionCache()
    application = OperatorApplication(repository, empty_gateway(), cache=cache)

    assert application.cache is cache


def test_shared_cache_invalidates_when_ducklake_snapshot_advances(repository) -> None:
    """Fresh request applications must not reuse a prior workspace projection."""

    campaign = compile_campaign(repository)
    cache = ProjectionCache()
    before = OperatorApplication(
        repository, campaign.gateway, cache=cache
    ).get_campaign_snapshot(
        "canonicalization-gate"
    )
    accepted_before = next(
        item for item in before.lens.stages if item.stage == "accepted_bindings"
    )

    second = campaign.run()
    assert second.success

    after = OperatorApplication(
        repository, campaign.gateway, cache=cache
    ).get_campaign_snapshot(
        "canonicalization-gate"
    )
    accepted_after = next(
        item for item in after.lens.stages if item.stage == "accepted_bindings"
    )

    assert accepted_before.latest_run_status == "succeeded"
    assert accepted_after.latest_run_status == "succeeded"
    assert accepted_after.latest_run_id == second.run_id
    assert accepted_after.latest_run_number > accepted_before.latest_run_number


def test_new_override_is_visible_as_uncompiled_decision(repository) -> None:
    campaign = compile_campaign(repository)
    campaign.overrides.revision = 1
    campaign.overrides.capture_id = "capture-old"

    before = OperatorApplication(repository, campaign.gateway).get_campaign_snapshot(
        "canonicalization-gate"
    ).lens.gates[0]
    assert before.status == "stale"
    assert before.selected_capture_id == "capture-new"
    assert before.active_override == "capture-old"
    assert "override revision advanced" in before.selection_reason

    campaign.run()
    after = OperatorApplication(repository, campaign.gateway).get_campaign_snapshot(
        "canonicalization-gate"
    ).lens.gates[0]
    assert after.status == "canonicalized"
    assert after.selected_capture_id == "capture-old"
    assert after.binding_source == "override"


def test_combined_lens_accounts_for_quarantined_bronze_input(repository) -> None:
    repository.replace_bronze_captures(
        [
            CaptureObservation(
                capture_id="capture-quarantine",
                series_namespace="anilist",
                series_id="1000",
                manifest_bucket="bronze",
                manifest_key="audio/anime/bronze/1000/metadata/odd.json",
                manifest_etag="etag-odd",
                manifest_schema_version=2,
                manifest_modified_at=datetime(2026, 1, 1, tzinfo=UTC),
                observed_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
        ],
        "bronze-quarantine",
    )
    repository.replace_resolution_tables(
        ResolutionBatch(
            hints=(),
            proposals=(),
            issues=(
                ResolutionIssueClaim(
                    issue_id="issue-1",
                    capture_id="capture-quarantine",
                    hint_id=None,
                    kind="no_ordinary_episode",
                    details={"path": "special"},
                ),
            ),
        ),
        "resolution-quarantine",
    )

    application = OperatorApplication(repository, empty_gateway())
    lens = application.get_campaign_snapshot(
        "canonicalization-gate"
    ).lens
    issue_page = application.get_stage_results("canonicalization-gate", "episode_resolution")

    assert lens.progress.captures == 1
    assert lens.progress.proposals == 0
    assert lens.progress.quarantined == 1
    assert issue_page.items[0].kind == "no_ordinary_episode"
    assert issue_page.items[0].details_json == '{"path": "special"}'


def test_json_html_htmx_and_static_asset_share_one_snapshot(repository) -> None:
    campaign = compile_campaign(repository)
    application = OperatorApplication(repository, campaign.gateway)
    app = create_operator_app(runtime_factory=NoopOperatorRuntime)
    app.dependency_overrides[get_application] = lambda: application

    with TestClient(app) as client:
        payload = client.get(
            "/api/operator/v1/campaigns/canonicalization-gate"
        )
        page = client.get("/operator/campaigns/canonicalization-gate")
        lens_url = "/operator/campaigns/canonicalization-gate/lens"
        fragment = client.get(lens_url, params={"series_id": "15451"})
        empty = client.get(lens_url, params={"series_id": "no-match"})
        reset = client.get(lens_url, params={"series_id": "  "})
        accepted = client.get("/operator/campaigns/canonicalization-gate/stages/accepted_bindings", params={"limit": 1})
        canonical = client.get("/operator/campaigns/canonicalization-gate/stages/canonical_inputs")
        product = client.get("/operator/campaigns/canonicalization-gate/products", params={"limit": 1})
        product_with_blank_view = client.get(
            "/operator/campaigns/canonicalization-gate/products",
            params={"limit": 50, "series_id": "", "run_id": ""},
        )
        candidates = client.get(
            "/operator/campaigns/canonicalization-gate/candidates/anilist/15451/3"
        )

    assert payload.status_code == 200
    assert product_with_blank_view.status_code == 200
    assert payload.json()["lens"]["gates"][0]["selected_capture_id"] == "capture-new"
    assert payload.json()["lens"]["gates"][0]["series_url"] == "https://anilist.co/anime/15451"
    assert payload.json()["lens"]["gates"][0]["selected_manifest_url"].endswith("/new.json")
    assert payload.json()["lens"]["gate_limit"] == 50
    stale_snapshot = application.get_campaign_snapshot("canonicalization-gate")
    stale_lens = stale_snapshot.lens.model_copy(update={"gate_total": 4})
    stale_snapshot = stale_snapshot.model_copy(update={"lens": stale_lens})
    stale_route_html = templates.get_template("_canonicalization_lens.html").render(snapshot=stale_snapshot)
    assert 'hx-trigger="load"' in stale_route_html
    assert "SHOW PRODUCT" in stale_route_html
    request = SimpleNamespace(url=SimpleNamespace(path="/operator/campaigns/x/products"))
    product_html = templates.get_template("_canonical_product.html").render(snapshot=stale_snapshot, request=request)
    assert "SHOW PRODUCT" not in product_html
    assert page.status_code == 200
    assert "Binding &amp; canonicalization desk" in page.text
    assert "capture-new" in page.text
    assert 'hx-get="/operator/campaigns/canonicalization-gate/lens"' in page.text
    assert fragment.status_code == 200 and 'id="target-lens"' in fragment.text
    assert "anilist:15451" in fragment.text and ":03" in fragment.text
    assert 'href="https://anilist.co/anime/15451"' in fragment.text
    assert '<details class="desk-section" open>' in fragment.text
    assert 'class="candidate-toggle"' in fragment.text
    assert 'class="candidate-header"' in candidates.text
    assert 'class="candidate-row selected"' in candidates.text
    assert 'title="s3://bronze/audio/anime/bronze/15451/metadata/new.json"' in fragment.text
    assert "↗" not in fragment.text
    assert "proposal-capture-new" not in fragment.text
    assert "proposal-" in candidates.text
    assert fragment.text.index("Episode Binding Desk") < fragment.text.index("Pipeline Spine")
    assert "Resolver Proposals · Exceptions" in fragment.text and "SELECTED" in fragment.text
    assert "Automatic Acceptance · Accepted" in accepted.text and "NEXT" in accepted.text
    assert "Canonical Inputs · Selected" in canonical.text
    assert "rows 1–1 / 1" in product.text
    assert "NO BINDING INPUT" in empty.text
    assert "capture-new" in reset.text and "NO BINDING INPUT" not in reset.text


def test_unknown_campaign_is_404(repository) -> None:
    app = create_operator_app(runtime_factory=NoopOperatorRuntime)
    app.dependency_overrides[get_application] = lambda: OperatorApplication(
        repository, empty_gateway()
    )
    with TestClient(app) as client:
        response = client.get("/api/operator/v1/campaigns/nope")
    assert response.status_code == 404


def test_headless_application_uses_graph_campaign_and_dagster_runs(repository) -> None:
    campaign = compile_campaign(repository)
    application = OperatorApplication(repository, campaign.gateway)

    snapshot = application.get_campaign_snapshot("canonicalization-gate")
    assert snapshot.lens.progress.canonicalized == 1
    runs = application.list_runs(limit=10)
    assert runs.total == 1
    detail = application.get_run(runs.items[0].run_id)
    assert detail.status == "succeeded"
    assert detail.duration_ms is not None
    assert detail.dagster_url.endswith(detail.run_id)
