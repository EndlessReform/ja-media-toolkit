"""Loadable data-control-plane assets, jobs, and resources."""

from __future__ import annotations

import dagster as dg

from ja_media_data.orchestration.dagster.assets import (
    accepted_bindings,
    binding_overrides,
    bronze_manifests,
    canonical_inputs,
    episode_resolution,
    subtitle_lid,
)
from ja_media_data.orchestration.dagster.canary import resolution_canary_job
from ja_media_data.orchestration.dagster.e1b_job import e1b_delayed_vad
from ja_media_data.orchestration.dagster.runtime import (
    canary_runtime_resource,
    product_runtime_resource,
)


ASSETS = (
    bronze_manifests,
    binding_overrides,
    episode_resolution,
    accepted_bindings,
    canonical_inputs,
    subtitle_lid,
)

canonicalization_campaign = dg.define_asset_job(
    name="canonicalization_campaign",
    selection=dg.AssetSelection.assets(
        "canonical_episode_inputs", "canonical_subtitle_inputs"
    ).upstream(include_self=True).required_multi_asset_neighbors(),
    executor_def=dg.in_process_executor,
    tags={
        "ja_media/campaign": "canonicalization-gate",
        "ja_media/campaign_revision": "1",
        "ja_media/scope": "corpus",
    },
)

observe_campaign_inputs = dg.define_asset_job(
    name="observe_campaign_inputs",
    selection=dg.AssetSelection.assets("bronze_manifests", "binding_overrides"),
    executor_def=dg.in_process_executor,
)

canonicalization_from_acceptance = dg.define_asset_job(
    name="canonicalization_from_acceptance",
    selection=dg.AssetSelection.assets(
        "canonical_episode_inputs",
        "canonical_subtitle_inputs",
        "subtitle_language_results",
    ).required_multi_asset_neighbors(),
    executor_def=dg.in_process_executor,
    tags={
        "ja_media/campaign": "canonicalization",
        "ja_media/from": "canonical_inputs",
    },
)


def build_definitions(
    *,
    product_runtime: object = product_runtime_resource,
    canary_runtime: object = canary_runtime_resource,
) -> dg.Definitions:
    """Build production or injected definitions from the same asset graph."""

    return dg.Definitions(
        assets=ASSETS,
        jobs=(
            observe_campaign_inputs,
            canonicalization_campaign,
            canonicalization_from_acceptance,
            resolution_canary_job,
            e1b_delayed_vad,
        ),
        resources={
            "product_runtime": product_runtime,
            "canary_runtime": canary_runtime,
        },
    )


defs = build_definitions()
