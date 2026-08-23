"""Resolve one case from the latest committed canonical Silver product."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import os
from urllib.parse import quote, unquote

import duckdb

from ja_media_data.lakehouse import CatalogConfig, connect_catalog
from ja_media_data.lakehouse.time_travel import table_ref
from ja_media_data.products.lineage import MaterializationCatalog
from ja_media_data.settings import DataSettings, load_process_secrets
from ja_media_data.storage.bronze import BronzeStore, bronze_store_from_settings

from forced_alignment_retiming.cases import CanonicalCase


@contextmanager
def open_dev_inputs(
    data_config: Path,
) -> Iterator[tuple[duckdb.DuckDBPyConnection, BronzeStore]]:
    """Open read-only Silver and bronze clients for one bounded case."""

    previous = os.environ.get("JA_MEDIA_DATA_CONFIG")
    os.environ["JA_MEDIA_DATA_CONFIG"] = str(data_config.resolve())
    try:
        load_process_secrets()
        settings = DataSettings()
        config = CatalogConfig.from_settings(settings)
        config = CatalogConfig(
            postgres_url=_normalize_postgres_url(config.postgres_url),
            metadata_schema=config.metadata_schema,
            data_path=config.data_path,
            alias=config.alias,
            s3_endpoint_url=config.s3_endpoint_url,
            s3_region=config.s3_region,
            s3_key_id=config.s3_key_id,
            s3_secret=config.s3_secret,
        )
        connection = connect_catalog(
            config, initialize_catalog=False
        )
        try:
            yield connection, bronze_store_from_settings(settings)
        finally:
            connection.close()
    finally:
        if previous is None:
            os.environ.pop("JA_MEDIA_DATA_CONFIG", None)
        else:
            os.environ["JA_MEDIA_DATA_CONFIG"] = previous


def _normalize_postgres_url(value: str) -> str:
    """Encode special characters in the existing DEV password for URL parsing."""

    scheme, raw = value.split("://", 1)
    auth, target = raw.rsplit("@", 1)
    user, password = auth.split(":", 1)
    return (
        f"{scheme}://{quote(unquote(user), safe='')}:"
        f"{quote(unquote(password), safe='')}@{target}"
    )


def resolve_case(
    connection: duckdb.DuckDBPyConnection, case: CanonicalCase
) -> dict[str, object]:
    """Return the exact canonical row and Silver product identity for a case."""

    head = MaterializationCatalog(connection).current_head("canonical_inputs")
    if head is None or head.snapshot_id is None:
        raise RuntimeError("canonical_inputs has no committed snapshot")
    episodes = table_ref(
        "canonical_episode_inputs", snapshot_id=head.snapshot_id, alias="episode"
    )
    rows = connection.execute(
        f"""SELECT canonical_id, audio_capture_id, binding_id, binding_source,
                    manifest_bucket, manifest_key, audio_object_bucket,
                    audio_object_key, audio_stream_index, audio_codec,
                    audio_declared_language, input_fingerprint
               FROM {episodes}
              WHERE namespace = ? AND series_id = ? AND episode = ?""",
        [case.namespace, case.series_id, case.episode],
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            f"expected one canonical row for {case.namespace}:"
            f"{case.series_id}:{case.episode}, found {len(rows)}"
        )
    row = rows[0]
    return {
        "materialization_id": head.materialization_id,
        "snapshot_id": head.snapshot_id,
        "fingerprint": head.fingerprint,
        "computed_at": str(head.computed_at),
        "canonical_id": str(row[0]),
        "audio_capture_id": str(row[1]),
        "binding_id": str(row[2]),
        "binding_source": str(row[3]),
        "manifest_bucket": str(row[4]),
        "manifest_key": str(row[5]),
        "audio_object_bucket": str(row[6]),
        "audio_object_key": str(row[7]),
        "audio_stream_index": int(row[8]),
        "audio_codec": str(row[9]) if row[9] is not None else None,
        "audio_declared_language": str(row[10]) if row[10] is not None else None,
        "input_fingerprint": str(row[11]),
        "embedded_subtitles": _subtitle_rows(connection, head.snapshot_id, case),
    }


def _subtitle_rows(
    connection: duckdb.DuckDBPyConnection,
    snapshot_id: int,
    case: CanonicalCase,
) -> list[dict[str, object]]:
    subtitles = table_ref(
        "canonical_subtitle_inputs", snapshot_id=snapshot_id, alias="subtitle"
    )
    rows = connection.execute(
        f"""SELECT subtitle_input_id, object_bucket, object_key, stream_index,
                    codec, declared_language, input_fingerprint
               FROM {subtitles}
              WHERE namespace = ? AND series_id = ? AND episode = ?
              ORDER BY stream_index, subtitle_input_id""",
        [case.namespace, case.series_id, case.episode],
    ).fetchall()
    return [
        {
            "subtitle_input_id": str(row[0]),
            "object_bucket": str(row[1]),
            "object_key": str(row[2]),
            "stream_index": int(row[3]),
            "codec": str(row[4]) if row[4] is not None else None,
            "declared_language": str(row[5]) if row[5] is not None else None,
            "input_fingerprint": str(row[6]),
        }
        for row in rows
    ]
