"""Structural tests for the deliberately small collection asset graph."""

from __future__ import annotations

import dagster as dg

from ja_media_data.orchestration.dagster.canary import (
    _bounded_quarantine_examples,
)
from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.runtime import (
    CanaryRuntime,
    ProductRuntime,
    hardcoded_runtime_resource,
)
from ja_media_data.products.episode_resolution.compiler import ResolutionResult

from dagster_test_support import FakeMetadata, FakeStore, documents


EXPECTED_ASSETS = {
    "bronze_captures",
    "bronze_manifests",
    "binding_overrides",
    "episode_hints_auto",
    "episode_binding_proposals",
    "resolution_issues_auto",
    "accepted_bindings_auto",
    "canonical_episode_inputs",
    "canonical_subtitle_inputs",
    "subtitle_language_results",
}


def test_collection_graph_has_no_capture_or_locator_partitions() -> None:
    defs = build_definitions()
    graph = defs.resolve_asset_graph()

    assert {key.to_user_string() for key in defs.resolve_all_asset_keys()} == EXPECTED_ASSETS
    assert all(graph.get(key).partitions_def is None for key in graph.get_all_asset_keys())
    assert {
        key.to_user_string()
        for key in graph.get(dg.AssetKey("accepted_bindings_auto")).parent_keys
    } == {"episode_binding_proposals"}
    assert {
        key.to_user_string()
        for key in graph.get(dg.AssetKey("canonical_episode_inputs")).parent_keys
    } == {"accepted_bindings_auto", "binding_overrides", "bronze_captures"}


def test_canary_is_non_publishing(repository) -> None:
    source = documents()
    product_store = FakeStore(source)
    canary_store = FakeStore(source)
    runtime = ProductRuntime(
        store=product_store,
        metadata_provider=FakeMetadata(),
        frozen_documents=source,
        repository=repository,
    )
    defs = build_definitions(
        product_runtime=hardcoded_runtime_resource(runtime),
        canary_runtime=dg.ResourceDefinition.hardcoded_resource(
            CanaryRuntime(canary_store, FakeMetadata())
        ),
    )
    before = repository.connection.execute(
        "SELECT count(*) FROM materializations"
    ).fetchone()[0]

    result = defs.resolve_job_def("resolution_canary").execute_in_process(
        run_config={"ops": {"evaluate_resolution_canary": {"config": {"limit": 2}}}}
    )

    assert result.success
    assert result.output_for_node("evaluate_resolution_canary") == {
        "scope": "canary",
        "publishes": False,
        "selected": 2,
        "selected_capture_ids": ["capture-old", "capture-new"],
        "selection_fingerprint": result.output_for_node(
            "evaluate_resolution_canary"
        )["selection_fingerprint"],
        "proposed": 2,
        "quarantined": 0,
        "quarantine_reasons": {},
        "quarantine_examples": [],
    }
    assert repository.connection.execute(
        "SELECT count(*) FROM materializations"
    ).fetchone()[0] == before
    assert result.get_asset_materialization_events() == []
    assert canary_store.last_limit == 2


def test_canary_reports_bounded_quarantine_diagnostics() -> None:
    source = documents()
    defs = build_definitions(
        canary_runtime=dg.ResourceDefinition.hardcoded_resource(
            CanaryRuntime(FakeStore(source), FakeMetadata())
        ),
    )

    result = defs.resolve_job_def("resolution_canary").execute_in_process(
        run_config={"ops": {"evaluate_resolution_canary": {"config": {"limit": 3}}}}
    )

    assert result.success
    summary = result.output_for_node("evaluate_resolution_canary")
    assert summary["quarantine_reasons"] == {
        "bronze_manifest_failed_schema_validation": 1
    }
    assert summary["quarantine_examples"] == [
        {
            "capture_id": "capture-invalid",
            "series_id": "999",
            "stem": "invalid",
            "reason": "bronze_manifest_failed_schema_validation",
            "issue_kind": "invalid",
            "manifest_key": "audio/anime/bronze/999/metadata/invalid.json",
            "parsed_filename_title": None,
            "parser_episode": None,
            "explicit_episode_numbers": None,
            "episode_ranges": None,
            "declared_series": None,
            "declared_series_metadata": None,
            "error": (
                "manifest has no source_hint, source, audio name, or "
                "audio_tracks name"
            ),
        }
    ]


def test_canary_examples_cover_distinct_reasons_before_repeats() -> None:
    results = [
        ResolutionResult(
            capture_id=f"capture-{index}",
            series_id="101",
            stem=f"Example_Ep{index:02d}",
            classification="quarantined",
            reason=reason,
            locator=None,
            issue_kind="invalid",
            evidence={},
        )
        for index, reason in enumerate(
            ("common", "common", "rare-a", "common", "rare-b", "rare-c"),
            start=1,
        )
    ]

    examples = _bounded_quarantine_examples(results)

    assert [item["reason"] for item in examples[:4]] == [
        "common",
        "rare-a",
        "rare-b",
        "rare-c",
    ]
    assert len(examples) == 5
