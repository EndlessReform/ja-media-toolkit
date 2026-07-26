"""Execution semantics for the canonicalization collection assets."""

from __future__ import annotations

import dagster as dg

from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.runtime import (
    ProductRuntime,
    hardcoded_runtime_resource,
)
from ja_media_data.products.lineage import MaterializationCatalog

from dagster_test_support import (
    FakeMetadata,
    FakeOverrides,
    FakeStore,
    changed_documents,
    documents,
)


def _definitions(repository, store, source):
    runtime = ProductRuntime(
        store=store,
        metadata_provider=FakeMetadata(),
        frozen_documents=source,
        repository=repository,
    )
    resource = hardcoded_runtime_resource(runtime)
    return build_definitions(product_runtime=resource, canary_runtime=resource)


def _head(repository, target: str):
    return MaterializationCatalog(repository.connection).current_head(target)


def test_success_reuse_quarantine_and_competing_candidates(repository) -> None:
    source = documents()
    store = FakeStore(source)
    defs = _definitions(repository, store, source)
    instance = dg.DagsterInstance.ephemeral()

    observed = defs.resolve_job_def("observe_campaign_inputs").execute_in_process(
        instance=instance
    )
    first = defs.resolve_job_def("canonicalization_campaign").execute_in_process(
        instance=instance
    )
    repeated = defs.resolve_job_def("canonicalization_campaign").execute_in_process(
        instance=instance
    )

    assert observed.success and first.success and repeated.success
    assert repository.connection.execute(
        "SELECT count(*) FROM resolution_issues_auto"
    ).fetchone() == (1,)
    assert repository.connection.execute(
        "SELECT count(*) FROM episode_binding_proposals"
    ).fetchone() == (2,)
    assert repository.connection.execute(
        "SELECT audio_capture_id FROM canonical_episode_inputs"
    ).fetchone() == ("capture-new",)
    dispositions = {
        event.asset_key.to_user_string(): event.event_specific_data.materialization.metadata[
            "write_disposition"
        ].value
        for event in repeated.get_asset_materialization_events()
    }
    assert set(dispositions.values()) == {"reused"}
    assert all(
        event.event_specific_data.materialization.tags.get(
            "dagster/data_version"
        )
        for event in first.get_asset_materialization_events()
    )


def test_changed_source_version_advances_resolver_outputs(repository) -> None:
    source = documents()
    instance = dg.DagsterInstance.ephemeral()
    first_defs = _definitions(repository, FakeStore(source), source)
    first_defs.resolve_job_def("observe_campaign_inputs").execute_in_process(
        instance=instance
    )
    first = first_defs.resolve_job_def("canonicalization_campaign").execute_in_process(
        instance=instance
    )
    old_fingerprint = _head(repository, "episode_resolution").fingerprint

    changed = changed_documents(source)
    second_defs = _definitions(repository, FakeStore(changed), changed)
    observed = second_defs.resolve_job_def("observe_campaign_inputs").execute_in_process(
        instance=instance
    )
    second = second_defs.resolve_job_def("canonicalization_campaign").execute_in_process(
        instance=instance
    )

    assert first.success and observed.success and second.success
    assert _head(repository, "episode_resolution").fingerprint != old_fingerprint
    assert len(observed.get_asset_observation_events()) == 2


def test_failed_canonical_keeps_old_head_after_acceptance(repository) -> None:
    source = documents()
    store = FakeStore(source)
    instance = dg.DagsterInstance.ephemeral()
    initial_defs = _definitions(repository, store, source)
    initial = initial_defs.resolve_job_def("canonicalization_campaign").execute_in_process(
        instance=instance
    )
    assert initial.success
    old_canonical = _head(repository, "canonical_inputs").materialization_id

    changed = changed_documents(source)
    failing_store = FakeStore(changed)
    failing_store.fail_manifests = True
    failed = _definitions(repository, failing_store, changed).resolve_job_def(
        "canonicalization_campaign"
    ).execute_in_process(instance=instance, raise_on_error=False)

    assert not failed.success
    assert _head(repository, "accepted_bindings").run_id.startswith("dagster:")
    assert _head(repository, "canonical_inputs").materialization_id == old_canonical
    latest = instance.get_latest_materialization_events(
        [dg.AssetKey("canonical_episode_inputs")]
    )
    assert latest[dg.AssetKey("canonical_episode_inputs")].run_id == initial.run_id


def test_changed_override_then_failed_lid_keeps_old_lid_head(repository) -> None:
    source = documents()
    store = FakeStore(source)
    instance = dg.DagsterInstance.ephemeral()
    defs = _definitions(repository, store, source)
    defs.resolve_job_def("observe_campaign_inputs").execute_in_process(
        instance=instance
    )
    prior_override_version = instance.get_latest_data_version_record(
        dg.AssetKey("binding_overrides"), is_source=True
    ).event_log_entry.tags["dagster/data_version"]
    initial = defs.resolve_job_def("canonicalization_campaign").execute_in_process(
        instance=instance
    )
    assert initial.success
    initial_lid = defs.resolve_job_def(
        "canonicalization_from_acceptance"
    ).execute_in_process(instance=instance)
    assert initial_lid.success
    old_lid = _head(repository, "subtitle_lid").materialization_id

    overrides = FakeOverrides()
    overrides.revision = 1
    overrides.capture_id = "capture-old"
    repository.override_repository = overrides
    store.fail_text = True
    changed_defs = _definitions(repository, store, source)
    changed_defs.resolve_job_def("observe_campaign_inputs").execute_in_process(
        instance=instance
    )
    current_override_version = instance.get_latest_data_version_record(
        dg.AssetKey("binding_overrides"), is_source=True
    ).event_log_entry.tags["dagster/data_version"]
    failed = changed_defs.resolve_job_def(
        "canonicalization_from_acceptance"
    ).execute_in_process(instance=instance, raise_on_error=False)

    assert not failed.success
    assert prior_override_version != current_override_version
    assert repository.connection.execute(
        "SELECT audio_capture_id FROM canonical_episode_inputs"
    ).fetchone() == ("capture-old",)
    assert _head(repository, "subtitle_lid").materialization_id == old_lid
    latest = instance.get_latest_materialization_events(
        [dg.AssetKey("subtitle_language_results")]
    )
    assert latest[dg.AssetKey("subtitle_language_results")].run_id == initial_lid.run_id

    store.fail_text = False
    downstream = changed_defs.resolve_job_def(
        "canonicalization_from_acceptance"
    ).execute_in_process(instance=instance)
    assert downstream.success
    assert {
        event.asset_key.to_user_string()
        for event in downstream.get_asset_materialization_events()
    } == {
        "canonical_episode_inputs",
        "canonical_subtitle_inputs",
        "subtitle_language_results",
    }
