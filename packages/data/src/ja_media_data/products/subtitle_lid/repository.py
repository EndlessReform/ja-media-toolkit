"""Atomic persistence for subtitle language results."""

from dataclasses import asdict
import json

import duckdb

from ja_media_data.products.atomic import AtomicProductStore
from ja_media_data.products.materialization import (
    MaterializationContext,
    ProductCommitResult,
)
from ja_media_data.products.subtitle_lid.compiler import CompiledSubtitleLid


_COLUMNS = (
    "subtitle_input_id",
    "namespace",
    "series_id",
    "episode",
    "audio_capture_id",
    "language",
    "reason",
    "script_metrics",
    "sampled_metrics",
    "input_fingerprint",
    "recipe_version",
)


def replace_product(
    connection: duckdb.DuckDBPyConnection,
    product: CompiledSubtitleLid,
    context: MaterializationContext,
) -> ProductCommitResult:
    """Atomically replace all subtitle language results."""

    values = []
    for item in product.rows:
        raw = asdict(item)
        raw["script_metrics"] = json.dumps(
            raw["script_metrics"], ensure_ascii=False, sort_keys=True
        )
        raw["sampled_metrics"] = (
            json.dumps(raw["sampled_metrics"], ensure_ascii=False, sort_keys=True)
            if raw["sampled_metrics"] is not None
            else None
        )
        values.append(tuple(raw[column] for column in _COLUMNS))
    return AtomicProductStore(connection).replace(
        target="subtitle_lid",
        tables=(("subtitle_language_results", _COLUMNS, values),),
        fingerprint=product.fingerprint,
        rows=len(product.rows),
        context=context,
    )


def merge_product(
    connection: duckdb.DuckDBPyConnection,
    product: CompiledSubtitleLid,
    context: MaterializationContext,
) -> ProductCommitResult:
    """Merge selected subtitle/recipe heads without replacing other results."""

    values = []
    for item in product.rows:
        raw = asdict(item)
        raw["script_metrics"] = json.dumps(
            raw["script_metrics"], ensure_ascii=False, sort_keys=True
        )
        raw["sampled_metrics"] = (
            json.dumps(raw["sampled_metrics"], ensure_ascii=False, sort_keys=True)
            if raw["sampled_metrics"] is not None
            else None
        )
        values.append(tuple(raw[column] for column in _COLUMNS))
    return AtomicProductStore(connection).merge(
        target="subtitle_lid",
        table="subtitle_language_results",
        columns=_COLUMNS,
        values=values,
        key_columns=("subtitle_input_id", "recipe_version"),
        key_indexes=(0, 10),
        fingerprint=product.fingerprint,
        context=context,
    )
