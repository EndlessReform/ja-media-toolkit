"""Shared real-table and ephemeral-Dagster setup for operator integration tests."""

from __future__ import annotations

from dataclasses import dataclass

import dagster as dg

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.gateway import DagsterGateway
from ja_media_data.orchestration.dagster.runtime import (
    ProductRuntime,
    hardcoded_runtime_resource,
)

from dagster_test_support import FakeMetadata, FakeOverrides, FakeStore, documents


class NoopOperatorRuntime:
    """Lifespan stand-in when HTTP tests inject their own application service."""

    def close(self) -> None:
        """Match the production runtime shutdown contract."""


@dataclass
class CompiledCampaign:
    """Reusable real campaign fixture with one authoritative Dagster instance."""

    repository: DuckLakeRepository
    instance: dg.DagsterInstance
    gateway: DagsterGateway
    definitions: dg.Definitions
    store: FakeStore
    overrides: FakeOverrides

    def run(self, *, raise_on_error: bool = True):
        return self.definitions.resolve_job_def(
            "canonicalization_campaign"
        ).execute_in_process(
            instance=self.instance, raise_on_error=raise_on_error
        )


def compile_campaign(repository: DuckLakeRepository) -> CompiledCampaign:
    """Compile competing captures through the real Dagster asset campaign."""

    source = documents()
    store = FakeStore(source)
    overrides = FakeOverrides()
    repository.override_repository = overrides
    runtime = ProductRuntime(
        store=store, metadata_provider=FakeMetadata(), frozen_documents=source,
        repository=repository,
    )
    resource = hardcoded_runtime_resource(runtime)
    definitions = build_definitions(
        product_runtime=resource, canary_runtime=resource
    )
    instance = dg.DagsterInstance.local_temp()
    campaign = CompiledCampaign(
        repository, instance, DagsterGateway(instance), definitions, store, overrides
    )
    result = campaign.run()
    assert result.success
    return campaign


def empty_gateway() -> DagsterGateway:
    """Return an injected empty instance for domain-only projection tests."""

    return DagsterGateway(dg.DagsterInstance.local_temp())
