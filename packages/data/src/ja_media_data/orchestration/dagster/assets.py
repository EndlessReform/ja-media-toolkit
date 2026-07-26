"""Collection-level Dagster assets backed by extracted product compilers."""

from __future__ import annotations

import hashlib
import json

import dagster as dg

from ja_media_data.orchestration.dagster.metadata import (
    commit_context,
    event_metadata,
    input_heads,
    output,
)
from ja_media_data.orchestration.dagster.runtime import ProductRuntime
from ja_media_data.products.binding_acceptance.compiler import (
    ACCEPTANCE_POLICY_VERSION,
    compile_product as compile_acceptance_product,
)
from ja_media_data.products.binding_acceptance.repository import (
    replace_product as replace_acceptance_product,
)
from ja_media_data.products.canonical_inputs.compiler import (
    CANONICALIZATION_POLICY_VERSION,
    binding_override_revision,
    compile_product as compile_canonical_product,
)
from ja_media_data.products.canonical_inputs.repository import (
    replace_product as replace_canonical_product,
)
from ja_media_data.products.episode_resolution.compiler import resolve_batch
from ja_media_data.products.episode_resolution.policy import RECIPE_VERSION


RESOURCE = {"product_runtime"}


@dg.observable_source_asset(
    name="bronze_manifests",
    group_name="bronze",
    required_resource_keys=RESOURCE,
    description="Complete collection of committed bronze manifests in object storage.",
)
def bronze_manifests(context) -> dg.ObserveResult:
    """Observe a collection version without creating capture-key partitions."""

    runtime = _runtime(context)
    documents = runtime.corpus_documents()
    fingerprint = _document_selection_fingerprint(documents)
    return dg.ObserveResult(
        data_version=dg.DataVersion(fingerprint),
        metadata={"captures": len(documents), "scope": "corpus"},
    )


@dg.observable_source_asset(
    name="binding_overrides",
    group_name="control",
    required_resource_keys=RESOURCE,
    description="Current transactional human binding-override revision.",
)
def binding_overrides(context) -> dg.ObserveResult:
    """Observe the compact PostgreSQL revision rather than copying its rows."""

    revision = binding_override_revision(_runtime(context).product_repository())
    return dg.ObserveResult(
        data_version=dg.DataVersion(str(revision)), metadata={"revision": revision}
    )


@dg.multi_asset(
    name="compile_episode_resolution",
    deps=[bronze_manifests],
    outs={
        "bronze_captures": dg.AssetOut(code_version=RECIPE_VERSION),
        "episode_hints_auto": dg.AssetOut(code_version=RECIPE_VERSION),
        "episode_binding_proposals": dg.AssetOut(
            code_version=RECIPE_VERSION
        ),
        "resolution_issues_auto": dg.AssetOut(code_version=RECIPE_VERSION),
    },
    group_name="silver",
    required_resource_keys=RESOURCE,
)
def episode_resolution(context):
    """Compile all resolver outcomes; quarantine remains a successful output."""

    runtime = _runtime(context)
    repository = runtime.product_repository()
    result = resolve_batch(
        runtime.corpus_documents(),
        store=runtime.store,
        metadata_provider=runtime.metadata_provider,
        repository=repository,
        run_source=context.run.run_id,
    )
    write = result.resolution_write
    bronze_write = result.bronze_write
    if write is None or bronze_write is None:
        raise RuntimeError("corpus resolution did not return durable commit results")
    counts = {
        "episode_hints_auto": write.hints,
        "episode_binding_proposals": write.proposals,
        "resolution_issues_auto": write.issues,
    }
    yield output(
        output_name="bronze_captures",
        fingerprint=bronze_write.fingerprint,
        metadata=event_metadata(
            repository,
            target="bronze_captures",
            written=bronze_write.written,
            rows=bronze_write.rows,
        ),
    )
    common = event_metadata(
        repository,
        target="episode_resolution",
        written=write.written,
        rows=sum(counts.values()),
        extra={
            "bronze_fingerprint": bronze_write.fingerprint,
            "bronze_written": bronze_write.written,
            "captures": bronze_write.rows,
        },
    )
    for name, rows in counts.items():
        yield output(
            output_name=name,
            fingerprint=write.fingerprint,
            metadata={**common, "rows": rows},
        )


@dg.asset(
    name="accepted_bindings_auto",
    deps=["episode_binding_proposals"],
    group_name="silver",
    required_resource_keys=RESOURCE,
    code_version=ACCEPTANCE_POLICY_VERSION,
)
def accepted_bindings(context) -> dg.MaterializeResult:
    """Apply the automatic proposal-admission policy to the full collection."""

    runtime = _runtime(context)
    repository = runtime.product_repository()
    heads = input_heads(repository, "episode_resolution")
    product = compile_acceptance_product(repository.connection)
    committed = replace_acceptance_product(
        repository.connection,
        product,
        commit_context(
            context,
            target="accepted_bindings",
            recipe_revision=ACCEPTANCE_POLICY_VERSION,
            input_heads=heads,
        ),
    )
    return dg.MaterializeResult(
        data_version=dg.DataVersion(product.fingerprint),
        metadata=event_metadata(
            repository,
            target="accepted_bindings",
            written=committed.written,
            rows=committed.rows,
        ),
    )


@dg.multi_asset(
    name="compile_canonical_inputs",
    deps=[accepted_bindings, binding_overrides, "bronze_captures"],
    outs={
        "canonical_episode_inputs": dg.AssetOut(
            code_version=CANONICALIZATION_POLICY_VERSION
        ),
        "canonical_subtitle_inputs": dg.AssetOut(
            code_version=CANONICALIZATION_POLICY_VERSION
        ),
    },
    group_name="silver",
    required_resource_keys=RESOURCE,
)
def canonical_inputs(context):
    """Select canonical episodes and subtitles as one atomic domain product."""

    runtime = _runtime(context)
    repository = runtime.product_repository()
    heads = input_heads(repository, "accepted_bindings", "bronze_captures")
    heads["binding_overrides"] = {
        "revision": binding_override_revision(repository)
    }
    product = compile_canonical_product(repository, runtime.store)
    committed = replace_canonical_product(
        repository.connection,
        product,
        commit_context(
            context,
            target="canonical_inputs",
            recipe_revision=CANONICALIZATION_POLICY_VERSION,
            input_heads=heads,
        ),
    )
    common = event_metadata(
        repository,
        target="canonical_inputs",
        written=committed.written,
        rows=committed.rows,
    )
    yield output(
        output_name="canonical_episode_inputs",
        fingerprint=product.fingerprint,
        metadata={**common, "rows": len(product.episodes)},
    )
    yield output(
        output_name="canonical_subtitle_inputs",
        fingerprint=product.fingerprint,
        metadata={**common, "rows": len(product.subtitles)},
    )


def _runtime(context: object) -> ProductRuntime:
    resources = getattr(context, "resources")
    return resources.product_runtime


def _document_selection_fingerprint(documents: object) -> str:
    rows = [
        (
            item.marker.capture_id,
            item.marker.key,
            item.marker.etag,
            item.marker.size,
            item.marker.last_modified,
        )
        for item in documents
    ]
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
