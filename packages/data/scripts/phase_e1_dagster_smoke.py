"""Run the E1 collection graph over 100 live source captures, locally isolated.

This operational spike intentionally lives under ``scripts`` until the E1
decision is accepted. It reads configured Garage/AniList sources, creates a
unique catalog schema in the disposable local PostgreSQL test database, writes
DuckLake files under ``/tmp``, and never opens the shared DuckLake catalog.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import uuid

import dagster as dg

from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.runtime import (
    CanaryRuntime,
    ProductRuntime,
    hardcoded_runtime_resource,
)
from ja_media_data.products.episode_resolution.metadata import (
    AniListEpisodeMetadataProvider,
)
from ja_media_data.storage.bronze import bronze_store_from_env


CAPTURE_LIMIT = 100


def main() -> None:
    """Materialize, repeat, and report one isolated live collection."""

    total_started = time.perf_counter()
    store = bronze_store_from_env()
    scan_started = time.perf_counter()
    documents = tuple(store.scan_documents(limit=CAPTURE_LIMIT))
    source_scan_seconds = round(time.perf_counter() - scan_started, 3)
    if len(documents) != CAPTURE_LIMIT:
        raise RuntimeError(f"expected {CAPTURE_LIMIT} captures, found {len(documents)}")
    metadata = AniListEpisodeMetadataProvider()
    schema = "phase_e1_live_" + uuid.uuid4().hex
    data_path = (
        Path(tempfile.mkdtemp(prefix="ja-media-phase-e1-", dir="/tmp")) / "ducklake"
    )
    connection = connect_catalog(
        CatalogConfig(
            postgres_url=os.environ.get(
                "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
                "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
                "@127.0.0.1:55432/ja_media_lakehouse_test",
            ),
            metadata_schema=schema,
            data_path=str(data_path),
        )
    )
    try:
        apply_schema(connection)
        repository = DuckLakeRepository(connection)
        product_runtime = ProductRuntime(
            store=store,
            metadata_provider=metadata,
            frozen_documents=documents,
            repository=repository,
        )
        canary_runtime = CanaryRuntime(store, metadata, documents)
        defs = build_definitions(
            product_runtime=hardcoded_runtime_resource(product_runtime),
            canary_runtime=dg.ResourceDefinition.hardcoded_resource(canary_runtime),
        )
        instance = dg.DagsterInstance.ephemeral()
        observed = _timed(
            defs.resolve_job_def("observe_campaign_inputs"), instance=instance
        )
        first = _timed(
            defs.resolve_job_def("canonicalization_campaign"), instance=instance
        )
        repeated = _timed(
            defs.resolve_job_def("canonicalization_campaign"), instance=instance
        )
        canary = defs.resolve_job_def("resolution_canary").execute_in_process(
            instance=instance,
            run_config={
                "ops": {"evaluate_resolution_canary": {"config": {"limit": 100}}}
            },
        )
        if not all(item[0].success for item in (observed, first, repeated)):
            raise RuntimeError("one or more E1 Dagster runs failed")
        if not canary.success:
            raise RuntimeError("E1 canary run failed")
        summary = repository.summary()
        expected = {
            "bronze_captures": 100,
            "episode_binding_proposals": 52,
            "resolution_issues_auto": 48,
            "accepted_bindings_auto": 52,
            "canonical_episode_inputs": 52,
            "canonical_subtitle_inputs": 89,
            "subtitle_language_results": 89,
        }
        actual = {name: summary[name] for name in expected}
        if actual != expected:
            raise RuntimeError(f"live slice changed: expected {expected}, found {actual}")
        dispositions = sorted(
            {
                event.event_specific_data.materialization.metadata[
                    "write_disposition"
                ].value
                for event in repeated[0].get_asset_materialization_events()
            }
        )
        canary_output = canary.output_for_node("evaluate_resolution_canary")
        print(
            json.dumps(
                {
                    "catalog_schema": schema,
                    "data_path": str(data_path),
                    "rows": actual,
                    "source_scan_seconds": source_scan_seconds,
                    "observe_seconds": observed[1],
                    "first_seconds": first[1],
                    "repeat_seconds": repeated[1],
                    "repeat_dispositions": dispositions,
                    "canary": {
                        key: canary_output[key]
                        for key in (
                            "scope",
                            "publishes",
                            "selected",
                            "proposed",
                            "quarantined",
                            "selection_fingerprint",
                        )
                    },
                    "canary_asset_materializations": len(
                        canary.get_asset_materialization_events()
                    ),
                    "total_seconds": round(time.perf_counter() - total_started, 3),
                },
                sort_keys=True,
            )
        )
    finally:
        connection.close()


def _timed(job: dg.JobDefinition, *, instance: dg.DagsterInstance):
    started = time.perf_counter()
    result = job.execute_in_process(instance=instance, raise_on_error=False)
    return result, round(time.perf_counter() - started, 3)


if __name__ == "__main__":
    main()
