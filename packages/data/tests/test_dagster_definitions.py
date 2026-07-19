"""Structural tests for the deliberately small E1 Dagster graph."""

from __future__ import annotations

import dagster as dg

from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.runtime import (
    CanaryRuntime,
    ProductRuntime,
    hardcoded_runtime_resource,
)

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
    }
    assert repository.connection.execute(
        "SELECT count(*) FROM materializations"
    ).fetchone()[0] == before
    assert result.get_asset_materialization_events() == []
    assert canary_store.last_limit == 2
