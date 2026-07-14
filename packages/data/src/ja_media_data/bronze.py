"""Capture-keyed Dagster definitions for externally committed bronze media.

Bronze is written by ingest, not by Dagster.  The asset specification makes
that ownership visible in the catalog while the repair sensor reports new or
changed commit manifests as external materializations.
"""

import json
import os

import dagster as dg

from ja_media_data.bronze_store import BronzeMarker, BronzeStore


BRONZE_CAPTURE_PARTITIONS = dg.DynamicPartitionsDefinition(name="bronze_capture_ids")


def bronze_store_from_env() -> BronzeStore:
    """Build the Garage reader from deployment configuration.

    AWS credentials keep their standard boto3 environment names.  The bucket
    is explicit because an endpoint may host unrelated datasets and listing all
    buckets is not a portable authorization assumption.
    """

    bucket = os.environ.get("JA_MEDIA_BRONZE_BUCKET")
    if not bucket:
        raise RuntimeError("JA_MEDIA_BRONZE_BUCKET must name the bronze bucket")
    return BronzeStore(
        endpoint_url=os.environ.get(
            "JA_MEDIA_S3_ENDPOINT_URL", "http://magi06-storage:3900"
        ),
        bucket=bucket,
        prefix=os.environ.get("JA_MEDIA_BRONZE_PREFIX", "audio/anime/bronze"),
        addressing_style=os.environ.get("JA_MEDIA_S3_ADDRESSING_STYLE", "path"),
    )


bronze_capture = dg.AssetSpec(
    key="bronze_capture",
    group_name="bronze",
    partitions_def=BRONZE_CAPTURE_PARTITIONS,
    description=(
        "A committed bronze capture created by ingest outside Dagster, keyed by "
        "durable capture ID."
    ),
    kinds={"s3", "bronze"},
)


@dg.sensor(
    minimum_interval_seconds=300,
    default_status=dg.DefaultSensorStatus.STOPPED,
    description="Repair scan for newly committed or changed bronze manifests.",
)
def bronze_scan_sensor(context: dg.SensorEvaluationContext) -> dg.SensorResult:
    """Register partitions and report one external materialization per ETag."""

    store = bronze_store_from_env()
    state = (
        json.loads(context.cursor)
        if context.cursor
        else {"markers": {}, "requested": []}
    )
    cached = {
        key: (value["etag"], value["capture_id"])
        for key, value in state["markers"].items()
    }
    markers = list(store.scan(cached))
    if not markers:
        return dg.SensorResult(skip_reason="No committed bronze manifests found")

    known = set(context.instance.get_dynamic_partitions(BRONZE_CAPTURE_PARTITIONS.name))
    new_keys = sorted({marker.capture_id for marker in markers} - known)
    requested = set(state["requested"])
    pending = [
        marker for marker in markers if f"{marker.capture_id}:{marker.etag}" not in requested
    ]
    event_limit = int(
        os.environ.get(
            "JA_MEDIA_BRONZE_SCAN_EVENT_LIMIT",
            os.environ.get("JA_MEDIA_BRONZE_SCAN_RUN_LIMIT", "25"),
        )
    )
    selected = pending[:event_limit]
    requested.update(f"{marker.capture_id}:{marker.etag}" for marker in selected)
    next_state = {
        "markers": {
            marker.key: {"etag": marker.etag, "capture_id": marker.capture_id}
            for marker in markers
        },
        "requested": sorted(requested),
    }
    return dg.SensorResult(
        dynamic_partitions_requests=(
            [BRONZE_CAPTURE_PARTITIONS.build_add_request(new_keys)] if new_keys else []
        ),
        asset_events=[
            _external_materialization(
                marker,
                store.read_manifest(marker.key, expected_etag=marker.etag),
            )
            for marker in selected
        ],
        cursor=json.dumps(next_state, separators=(",", ":"), sort_keys=True),
    )


def _external_materialization(
    marker: BronzeMarker, manifest: dict[str, object]
) -> dg.AssetMaterialization:
    """Describe one committed manifest without claiming Dagster produced it."""

    capture_id = manifest.get("capture_id", marker.capture_id)
    if capture_id != marker.capture_id:
        raise ValueError(
            f"manifest capture_id {capture_id!r} does not match discovered capture "
            f"{marker.capture_id!r}"
        )
    series = manifest.get("series", {})
    series = series if isinstance(series, dict) else {}
    subtitles = manifest.get("subtitles", [])
    subtitles = subtitles if isinstance(subtitles, list) else []
    return dg.AssetMaterialization(
        asset_key=bronze_capture.key,
        partition=marker.capture_id,
        metadata={
            "capture_id": marker.capture_id,
            "manifest_key": marker.key,
            "manifest_etag": marker.etag,
            "manifest_size_bytes": marker.size,
            "manifest_last_modified": marker.last_modified,
            "schema_version": manifest.get("schema_version", 1),
            "subtitle_tracks": len(subtitles),
            "series_namespace": series.get("namespace", "unknown"),
            "series_id": str(series.get("id", "unknown")),
        },
        tags={
            "dagster/data_version": marker.etag,
        },
    )
