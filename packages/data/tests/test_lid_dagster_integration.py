"""Remote-shaped, locally executed Dagster-to-worker LID integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import boto3
from botocore.config import Config
from celery.contrib.testing.worker import start_worker
import dagster as dg
import pytest

from ja_media_data.orchestration.dagster.definitions import build_definitions
from ja_media_data.orchestration.dagster.runtime import (
    ProductRuntime,
    hardcoded_runtime_resource,
)
from ja_media_data.products.canonical_inputs.compiler import (
    CompiledCanonicalInputs,
)
from ja_media_data.products.canonical_inputs.models import CanonicalSubtitleInput
from ja_media_data.products.canonical_inputs.repository import replace_product
from ja_media_data.products.identities import fingerprint
from ja_media_data.products.materialization import MaterializationContext
from ja_media_data.storage.bronze import BronzeStore
from ja_media_data.workers.app import build_worker_app
from ja_media_data.workers.lid_handoff import CeleryLidHandoff
from ja_media_data.workers.marker_store import MarkerStore
from ja_media_data.workers.settings import (
    WorkerProfile,
    WorkerSecrets,
    WorkerSettings,
)

from dagster_test_support import FakeMetadata


BUCKET = "ja-media-lakehouse-test"
ENDPOINT = "http://127.0.0.1:59000"
ACCESS_KEY = "ja_media_lakehouse_test"
SECRET_KEY = "ja-media-lakehouse-test-only"


def test_dev_canonical_seed_crosses_dagster_queue_and_product_merge(
    repository,
) -> None:
    """Run a bounded DEV canonical snapshot entirely through local services."""

    seed_path = os.environ.get("JA_MEDIA_LID_SEED_PATH")
    if not seed_path:
        pytest.skip("set JA_MEDIA_LID_SEED_PATH to a bounded exported DEV seed")
    seed = json.loads(Path(seed_path).read_text())
    records = seed["records"]
    assert records
    token = uuid.uuid4().hex
    prefix = f"worker-staging/test/{token}"
    queue = f"cpu-light-integration-{token}"
    s3 = _s3()
    source_keys = [item["canonical"]["object_key"] for item in records]
    for item in records:
        s3.put_object(
            Bucket=BUCKET,
            Key=item["canonical"]["object_key"],
            Body=item["body"].encode(),
        )
    try:
        _seed_canonical_product(repository, records)
        bronze = BronzeStore(
            endpoint_url=ENDPOINT,
            bucket=BUCKET,
            prefix="audio/anime/bronze",
            addressing_style="path",
            access_key_id=ACCESS_KEY,
            secret_access_key=SECRET_KEY,
        )
        markers = MarkerStore(
            endpoint_url=ENDPOINT,
            bucket=BUCKET,
            prefix=prefix,
            region="us-east-1",
            addressing_style="path",
            access_key_id=ACCESS_KEY,
            secret_access_key=SECRET_KEY,
        )
        handoff = CeleryLidHandoff(
            broker_url="pyamqp://guest@127.0.0.1:5672//",
            markers=markers,
            queue=queue,
            timeout_seconds=15,
            poll_seconds=0.05,
        )
        runtime = ProductRuntime(
            store=bronze,
            metadata_provider=FakeMetadata(),
            repository=repository,
            lid_handoff=handoff,
        )
        resource = hardcoded_runtime_resource(runtime)
        definitions = build_definitions(
            product_runtime=resource, canary_runtime=resource
        )
        worker = build_worker_app(_worker_settings(queue, prefix))
        with start_worker(
            worker, pool="solo", concurrency=1, perform_ping_check=False
        ):
            first = _execute_lid(definitions, len(records))
            repeated = _execute_lid(definitions, len(records))

        assert first.success and repeated.success
        assert repository.connection.execute(
            "SELECT count(*) FROM subtitle_language_results"
        ).fetchone() == (len(records),)
        assert repository.connection.execute(
            """SELECT count(*) FROM worker_handoff_items
               WHERE disposition = 'succeeded'"""
        ).fetchone() == (len(records),)
    finally:
        objects = [{"Key": key} for key in source_keys]
        objects.extend(
            {"Key": item["Key"]}
            for item in s3.list_objects_v2(
                Bucket=BUCKET, Prefix=prefix + "/"
            ).get("Contents", [])
        )
        if objects:
            s3.delete_objects(Bucket=BUCKET, Delete={"Objects": objects})


def _execute_lid(definitions, limit: int):
    return definitions.resolve_job_def(
        "subtitle_lid_from_canonical"
    ).execute_in_process(
        instance=dg.DagsterInstance.ephemeral(),
        run_config={
            "ops": {
                "subtitle_language_results": {"config": {"limit": limit}}
            }
        },
    )


def _seed_canonical_product(repository, records: list[dict[str, object]]) -> None:
    subtitles = []
    for item in records:
        row = dict(item["canonical"])
        row["object_bucket"] = BUCKET
        subtitles.append(CanonicalSubtitleInput(**row))
    product = CompiledCanonicalInputs(
        episodes=(),
        subtitles=tuple(subtitles),
        fingerprint=fingerprint("dev-canonical-seed", subtitles),
    )
    replace_product(
        repository.connection,
        product,
        MaterializationContext(
            attempt_id="test:dev-canonical-seed",
            pipeline_run_id="test",
            recipe_revision="latest-manifest-modified-v1",
            build_key="dev-canonical-seed",
            input_heads={"source": "dev-read-only-export"},
        ),
    )


def _worker_settings(queue: str, prefix: str) -> WorkerSettings:
    return WorkerSettings(
        profile=WorkerProfile(
            name="integration",
            broker_host="127.0.0.1",
            broker_port=5672,
            broker_user="guest",
            broker_vhost="/",
            queue=queue,
            operations=("subtitle_language_id",),
            concurrency=1,
            staging_bucket=BUCKET,
            staging_prefix=prefix,
            staging_region="us-east-1",
        ),
        secrets=WorkerSecrets(
            rabbitmq_password="guest",
            bronze_access_key_id=ACCESS_KEY,
            bronze_secret_access_key=SECRET_KEY,
            staging_access_key_id=ACCESS_KEY,
            staging_secret_access_key=SECRET_KEY,
        ),
        bronze_endpoint_url=ENDPOINT,
        bronze_bucket=BUCKET,
        bronze_prefix="audio/anime/bronze",
        bronze_addressing_style="path",
        loaded_files=(),
    )


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(s3={"addressing_style": "path"}),
    )
