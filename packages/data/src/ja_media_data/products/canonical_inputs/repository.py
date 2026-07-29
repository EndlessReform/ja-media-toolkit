"""Atomic persistence for canonical episode and subtitle inputs."""

from dataclasses import asdict

import duckdb

from ja_media_data.products.atomic import AtomicProductStore
from ja_media_data.products.canonical_inputs.compiler import CompiledCanonicalInputs
from ja_media_data.products.materialization import (
    MaterializationContext,
    ProductCommitResult,
)


_EPISODE_COLUMNS = (
    "canonical_id",
    "namespace",
    "series_id",
    "episode",
    "audio_capture_id",
    "binding_id",
    "binding_source",
    "manifest_bucket",
    "manifest_key",
    "manifest_etag",
    "manifest_modified_at",
    "audio_object_bucket",
    "audio_object_key",
    "audio_stream_index",
    "audio_codec",
    "audio_declared_language",
    "input_fingerprint",
)
_SUBTITLE_COLUMNS = (
    "subtitle_input_id",
    "canonical_id",
    "namespace",
    "series_id",
    "episode",
    "audio_capture_id",
    "object_bucket",
    "object_key",
    "stream_index",
    "codec",
    "declared_language",
    "input_fingerprint",
)


def replace_product(
    connection: duckdb.DuckDBPyConnection,
    product: CompiledCanonicalInputs,
    context: MaterializationContext,
) -> ProductCommitResult:
    """Replace both canonical tables in one transaction."""

    episodes = [
        tuple(asdict(item)[column] for column in _EPISODE_COLUMNS)
        for item in product.episodes
    ]
    subtitles = [
        tuple(asdict(item)[column] for column in _SUBTITLE_COLUMNS)
        for item in product.subtitles
    ]
    return AtomicProductStore(connection).replace(
        target="canonical_inputs",
        tables=(
            ("canonical_episode_inputs", _EPISODE_COLUMNS, episodes),
            ("canonical_subtitle_inputs", _SUBTITLE_COLUMNS, subtitles),
        ),
        fingerprint=product.fingerprint,
        rows=len(product.episodes),
        context=context,
    )
