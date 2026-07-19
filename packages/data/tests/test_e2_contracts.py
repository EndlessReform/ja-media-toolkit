"""E2.0 contract-freeze and E2.1 mixed-generation acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys

import dagster as dg
from fastapi.testclient import TestClient
import pytest

from ja_media_data.operator.application import OperatorApplication
from ja_media_data.operator.campaigns import CampaignCatalog
from ja_media_data.operator.http.app import create_operator_app
from ja_media_data.operator.http.dependencies import get_application
from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.workers.contracts import ObjectRef, VadRequest, WorkEnvelope
from ja_media_data.workers.invocation import invoke_environment

from operator_test_support import NoopOperatorRuntime, compile_campaign


def test_campaign_toml_resolves_spine_from_asset_graph() -> None:
    campaign = CampaignCatalog(build_definitions()).get("canonicalization-gate")

    assert campaign.spec.revision == 1
    assert [item.stage for item in campaign.spine] == [
        "episode_resolution", "accepted_bindings", "canonical_inputs"
    ]
    assert [item.op_name for item in campaign.spine] == [
        "compile_episode_resolution", "accepted_bindings_auto",
        "compile_canonical_inputs",
    ]


def test_campaign_rejects_an_unknown_asset(tmp_path) -> None:
    (tmp_path / "bad.toml").write_text(
        """schema_version = 1
campaign_id = "bad-campaign"
revision = 1
label = "Bad"
description = "Bad reference"
job_name = "canonicalization_campaign"
target_assets = ["missing_asset"]
scope_kind = "corpus"
lens_kind = "canonicalization"
"""
    )

    with pytest.raises(ValueError, match="missing assets"):
        CampaignCatalog(build_definitions(), tmp_path)


def test_environment_command_round_trips_without_control_plane_credentials() -> None:
    envelope = _envelope()
    command = (sys.executable, str(Path(__file__).parent / "fixtures/worker_echo.py"))

    result = invoke_environment(
        envelope, command, environment={"JA_MEDIA_WORKER_PROFILE": "apple-vad"}
    )

    assert result.request_id == envelope.request_id
    assert result.result.locator == "anilist:10087:01"
    assert result.result.chunks[0].object.key.endswith("/000.flac")


def test_environment_command_rejects_catalog_or_dagster_storage_credentials() -> None:
    with pytest.raises(ValueError, match="control-plane credentials"):
        invoke_environment(
            _envelope(), ("unused",),
            environment={"DAGSTER_POSTGRES_URL": "must-not-cross"},
        )


def test_failed_run_displays_advanced_item_heads(repository) -> None:
    campaign = compile_campaign(repository)

    @dg.op(name="compile_canonical_inputs")
    def fail_after_partial_commit():
        raise RuntimeError("fixture failure after 93 item commits")

    failed_job = dg.GraphDefinition(
        name="canonicalization_campaign",
        node_defs=[fail_after_partial_commit],
        dependencies={},
    ).to_job()
    failed = failed_job.execute_in_process(
        instance=campaign.instance, raise_on_error=False
    )
    assert not failed.success
    _insert_handoffs(repository, failed.run_id, succeeded=93, total=96)

    application = OperatorApplication(repository, campaign.gateway)
    run = application.get_run(failed.run_id)
    app = create_operator_app(
        initialize_schema=False, runtime_factory=NoopOperatorRuntime
    )
    app.dependency_overrides[get_application] = lambda: application
    with TestClient(app) as client:
        response = client.get(f"/operator/runs/{failed.run_id}")

    assert run.status == "failed"
    assert (run.items_succeeded, run.items_total) == (93, 96)
    assert response.status_code == 200
    assert "93 / 96 advanced" in response.text


def _envelope() -> WorkEnvelope:
    return WorkEnvelope(
        request_id="request-1", campaign_run_id="run-1", step_key="vad",
        attempt=1, requested_at=datetime.now(UTC),
        payload=VadRequest(
            locator="anilist:10087:01",
            source=ObjectRef(bucket="bronze", key="episode.flac", bytes=2048),
            output_prefix="staging/run-1/request-1",
            recipe_revision="vad-v1", model_id="silero-vad",
        ),
    )


def _insert_handoffs(repository, run_id: str, *, succeeded: int, total: int) -> None:
    now = datetime.now(UTC)
    rows = [
        (
            f"request-{index}", run_id, "asr", "asr_transcript",
            f"anilist:1:{index:02}", 1, f"input-{index}",
            f"output-{index}" if index < succeeded else None,
            "succeeded" if index < succeeded else "failed",
            None, None, now, now, now,
        )
        for index in range(total)
    ]
    repository.connection.executemany(
        "INSERT INTO worker_handoff_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
