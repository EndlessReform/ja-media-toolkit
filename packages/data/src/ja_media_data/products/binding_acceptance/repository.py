"""Atomic persistence for the automatic binding-admission product."""

from dataclasses import asdict

import duckdb

from ja_media_data.products.atomic import AtomicProductStore
from ja_media_data.products.binding_acceptance.compiler import CompiledAcceptances
from ja_media_data.products.materialization import MaterializationContext, ProductCommitResult


def replace_product(
    connection: duckdb.DuckDBPyConnection,
    product: CompiledAcceptances,
    context: MaterializationContext,
) -> ProductCommitResult:
    """Atomically replace all automatic acceptances."""

    values = [tuple(asdict(item).values()) for item in product.rows]
    return AtomicProductStore(connection).replace(
        target="accepted_bindings",
        tables=(("accepted_bindings_auto", 9, values),),
        fingerprint=product.fingerprint,
        rows=len(product.rows),
        context=context,
    )
