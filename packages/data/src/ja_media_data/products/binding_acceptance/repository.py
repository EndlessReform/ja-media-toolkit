"""Atomic persistence for the automatic binding-admission product."""

from dataclasses import asdict

import duckdb

from ja_media_data.products.atomic import AtomicProductStore
from ja_media_data.products.binding_acceptance.compiler import CompiledAcceptances
from ja_media_data.products.materialization import (
    MaterializationContext,
    ProductCommitResult,
)


_COLUMNS = (
    "acceptance_id",
    "proposal_id",
    "namespace",
    "series_id",
    "episode",
    "audio_capture_id",
    "acceptance_method",
    "policy_version",
    "input_fingerprint",
)


def replace_product(
    connection: duckdb.DuckDBPyConnection,
    product: CompiledAcceptances,
    context: MaterializationContext,
) -> ProductCommitResult:
    """Atomically replace all automatic acceptances."""

    values = [tuple(asdict(item)[column] for column in _COLUMNS) for item in product.rows]
    return AtomicProductStore(connection).replace(
        target="accepted_bindings",
        tables=(("accepted_bindings_auto", _COLUMNS, values),),
        fingerprint=product.fingerprint,
        rows=len(product.rows),
        context=context,
    )
