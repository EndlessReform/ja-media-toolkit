"""Dagster adapter for server-dispatched subtitle language identification."""

from __future__ import annotations

import dagster as dg

from ja_media_data.orchestration.dagster.metadata import (
    commit_context,
    event_metadata,
    input_heads,
)
from ja_media_data.orchestration.dagster.runtime import ProductRuntime
from ja_media_data.products.subtitle_lid.compiler import (
    SUBTITLE_LID_RECIPE_VERSION,
    compile_product,
)
from ja_media_data.products.subtitle_lid.repository import (
    merge_product,
    replace_product,
)


RESOURCE = {"product_runtime"}


@dg.asset(
    name="subtitle_language_results",
    deps=["canonical_subtitle_inputs"],
    group_name="silver",
    required_resource_keys=RESOURCE,
    code_version=SUBTITLE_LID_RECIPE_VERSION,
    config_schema={
        "limit": dg.Field(
            int,
            default_value=100,
            description="Maximum missing or stale subtitles dispatched this run.",
        )
    },
)
def subtitle_lid(context) -> dg.MaterializeResult:
    """Dispatch eligible subtitle LID items and merge verified results."""

    runtime: ProductRuntime = context.resources.product_runtime
    repository = runtime.product_repository()
    heads = input_heads(repository, "canonical_inputs")
    handoff = runtime.lid_handoff
    if handoff is None:
        product = compile_product(repository.connection, runtime.store)
        committed = replace_product(
            repository.connection,
            product,
            commit_context(
                context,
                target="subtitle_lid",
                recipe_revision=SUBTITLE_LID_RECIPE_VERSION,
                input_heads=heads,
            ),
        )
        selected = len(product.rows)
        failed = ()
    else:
        batch = handoff.execute(
            repository.connection,
            campaign_run_id=context.run.run_id,
            step_key="subtitle_language_results",
            limit=int(context.op_config["limit"]),
        )
        product = batch.product
        committed = merge_product(
            repository.connection,
            product,
            commit_context(
                context,
                target="subtitle_lid",
                recipe_revision=SUBTITLE_LID_RECIPE_VERSION,
                input_heads=heads,
            ),
        )
        selected = batch.selected
        failed = batch.failed_request_ids
    metadata = event_metadata(
        repository,
        target="subtitle_lid",
        written=committed.written,
        rows=committed.rows,
        extra={
            "selected": selected,
            "succeeded": len(product.rows),
            "failed": len(failed),
        },
    )
    if failed:
        context.log.error(
            "subtitle LID failed requests: %s", ", ".join(failed[:10])
        )
        raise dg.Failure(
            description=(
                f"{len(failed)} of {selected} subtitle LID items failed; "
                f"{len(product.rows)} successful rows were merged"
            ),
            metadata=metadata,
        )
    return dg.MaterializeResult(
        data_version=dg.DataVersion(product.fingerprint),
        metadata=metadata,
    )
