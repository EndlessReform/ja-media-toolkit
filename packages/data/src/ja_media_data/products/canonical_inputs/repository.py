"""Atomic persistence for canonical episode and subtitle inputs."""

from dataclasses import asdict

import duckdb

from ja_media_data.products.atomic import AtomicProductStore
from ja_media_data.products.canonical_inputs.compiler import CompiledCanonicalInputs
from ja_media_data.products.materialization import MaterializationContext, ProductCommitResult


def replace_product(
    connection: duckdb.DuckDBPyConnection,
    product: CompiledCanonicalInputs,
    context: MaterializationContext,
) -> ProductCommitResult:
    """Replace both canonical tables in one transaction."""

    episodes = [tuple(asdict(item).values()) for item in product.episodes]
    subtitles = [tuple(asdict(item).values()) for item in product.subtitles]
    return AtomicProductStore(connection).replace(
        target="canonical_inputs",
        tables=(
            ("canonical_episode_inputs", 12, episodes),
            ("canonical_subtitle_inputs", 12, subtitles),
        ),
        fingerprint=product.fingerprint,
        rows=len(product.episodes),
        context=context,
    )
