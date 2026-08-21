"""Atomic persistence for capture-audio eligibility decisions."""

from dataclasses import asdict

import duckdb

from ja_media_data.products.atomic import AtomicProductStore
from ja_media_data.products.capture_audio_eligibility.compiler import (
    CompiledCaptureAudioEligibility,
)
from ja_media_data.products.materialization import (
    MaterializationContext,
    ProductCommitResult,
)


_COLUMNS = (
    "capture_id",
    "manifest_bucket",
    "manifest_key",
    "manifest_etag",
    "manifest_schema_version",
    "status",
    "reason",
    "selected_audio_object_bucket",
    "selected_audio_object_key",
    "selected_audio_stream_index",
    "selected_audio_codec",
    "selected_audio_declared_language",
    "available_audio_tracks",
    "input_fingerprint",
)


def replace_product(
    connection: duckdb.DuckDBPyConnection,
    product: CompiledCaptureAudioEligibility,
    context: MaterializationContext,
) -> ProductCommitResult:
    """Replace the complete decision product in one transaction."""

    values = [
        tuple(asdict(item)[column] for column in _COLUMNS) for item in product.rows
    ]
    return AtomicProductStore(connection).replace(
        target="capture_audio_eligibility",
        tables=(("capture_audio_eligibility", _COLUMNS, values),),
        fingerprint=product.fingerprint,
        rows=len(product.rows),
        context=context,
    )
