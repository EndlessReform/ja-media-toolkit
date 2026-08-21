"""Dagster adapter for the capture-audio eligibility product."""

from collections import Counter

import dagster as dg

from ja_media_data.orchestration.dagster.metadata import (
    commit_context,
    event_metadata,
    input_heads,
)
from ja_media_data.orchestration.dagster.runtime import ProductRuntime
from ja_media_data.products.capture_audio_eligibility.compiler import (
    AUDIO_ELIGIBILITY_POLICY_VERSION,
    ELIGIBLE,
    compile_product,
)
from ja_media_data.products.capture_audio_eligibility.repository import replace_product


@dg.asset(
    name="capture_audio_eligibility",
    deps=["bronze_captures"],
    group_name="silver",
    required_resource_keys={"product_runtime"},
    code_version=AUDIO_ELIGIBILITY_POLICY_VERSION,
)
def capture_audio_eligibility(context) -> dg.MaterializeResult:
    """Publish capture decisions; an ineligible capture is successful output."""

    runtime: ProductRuntime = context.resources.product_runtime
    repository = runtime.product_repository()
    product = compile_product(list(runtime.corpus_documents()), runtime.store)
    committed = replace_product(
        repository.connection,
        product,
        commit_context(
            context,
            target="capture_audio_eligibility",
            recipe_revision=AUDIO_ELIGIBILITY_POLICY_VERSION,
            input_heads=input_heads(repository, "bronze_captures"),
        ),
    )
    statuses = Counter(row.status for row in product.rows)
    reasons = Counter(row.reason for row in product.rows if row.status != ELIGIBLE)
    language_sets = Counter(
        _language_set(row.available_audio_tracks)
        for row in product.rows
        if row.status != ELIGIBLE
    )
    return dg.MaterializeResult(
        data_version=dg.DataVersion(product.fingerprint),
        metadata=event_metadata(
            repository,
            target="capture_audio_eligibility",
            written=committed.written,
            rows=committed.rows,
            extra={
                "eligible": statuses[ELIGIBLE],
                "ineligible": committed.rows - statuses[ELIGIBLE],
                "ineligible_reasons": dict(sorted(reasons.items())),
                "ineligible_declared_language_sets": dict(
                    sorted(language_sets.items())
                ),
            },
        ),
    )


def _language_set(serialized_tracks: str) -> str:
    """Summarize declared languages without retaining every track field."""

    import json

    tracks = json.loads(serialized_tracks)
    languages = sorted(
        {track.get("declared_language") or "<missing>" for track in tracks}
    )
    return ",".join(languages) if languages else "<no-parseable-tracks>"
