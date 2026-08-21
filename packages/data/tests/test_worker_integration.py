"""Local RabbitMQ + MinIO integration test for the native LID worker."""

from __future__ import annotations

from datetime import UTC, datetime
import time
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from celery.contrib.testing.worker import start_worker

from ja_media_data.workers.app import TASK_NAME, build_worker_app
from ja_media_data.workers.contracts import (
    ObjectRef,
    ResultEnvelope,
    SubtitleLidRequest,
    WorkEnvelope,
)
from ja_media_data.workers.settings import (
    WorkerProfile,
    WorkerSecrets,
    WorkerSettings,
)


def test_lid_message_crosses_rabbitmq_and_commits_marker() -> None:
    """Exercise the real queue boundary without shared DEV data."""

    token = uuid.uuid4().hex
    source_key = f"worker-integration/{token}/episode.srt"
    marker_key = f"worker-staging/test/results/{token}.json"
    s3 = _local_s3()
    source = _subtitle()
    etag = s3.put_object(
        Bucket="ja-media-lakehouse-test",
        Key=source_key,
        Body=source.encode(),
    )["ETag"].strip('"')
    settings = _settings()
    envelope = WorkEnvelope(
        request_id=token,
        campaign_run_id="local-integration",
        step_key="subtitle_lid",
        attempt=1,
        requested_at=datetime.now(UTC),
        payload=SubtitleLidRequest(
            subtitle_input_id=f"subtitle-{token}",
            source=ObjectRef(
                bucket="ja-media-lakehouse-test", key=source_key, etag=etag
            ),
            codec="srt",
            input_fingerprint=f"fixture-{token}",
            recipe_revision="subtitle-script-fasttext-v1",
            staging_bucket="ja-media-lakehouse-test",
            staging_prefix="worker-staging/test",
        ),
    )
    try:
        app = build_worker_app(settings)
        with start_worker(
            app, pool="solo", concurrency=1, perform_ping_check=False
        ):
            app.send_task(
                TASK_NAME,
                args=[envelope.model_dump(mode="json")],
                queue=settings.profile.queue,
            )
            result = _wait_for_result(s3, marker_key)
        assert result.request_id == token
        assert result.result.operation == "subtitle_language_id"
        assert result.result.language.value == "japanese"
    finally:
        s3.delete_objects(
            Bucket="ja-media-lakehouse-test",
            Delete={"Objects": [{"Key": source_key}, {"Key": marker_key}]},
        )


def _settings() -> WorkerSettings:
    profile = WorkerProfile(
        name="integration",
        broker_host="127.0.0.1",
        broker_port=5672,
        broker_user="guest",
        broker_vhost="/",
        queue="cpu-light-integration",
        operations=("subtitle_language_id",),
        concurrency=1,
        staging_bucket="ja-media-lakehouse-test",
        staging_prefix="worker-staging/test",
        staging_region="us-east-1",
    )
    secrets = WorkerSecrets(
        rabbitmq_password="guest",
        bronze_access_key_id="ja_media_lakehouse_test",
        bronze_secret_access_key="ja-media-lakehouse-test-only",
        staging_access_key_id="ja_media_lakehouse_test",
        staging_secret_access_key="ja-media-lakehouse-test-only",
    )
    return WorkerSettings(
        profile=profile,
        secrets=secrets,
        bronze_endpoint_url="http://127.0.0.1:59000",
        bronze_bucket="ja-media-lakehouse-test",
        bronze_prefix="worker-integration",
        bronze_addressing_style="path",
        loaded_files=(),
    )


def _local_s3():
    return boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:59000",
        region_name="us-east-1",
        aws_access_key_id="ja_media_lakehouse_test",
        aws_secret_access_key="ja-media-lakehouse-test-only",
        config=Config(s3={"addressing_style": "path"}),
    )


def _wait_for_result(s3, key: str) -> ResultEnvelope:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            response = s3.get_object(Bucket="ja-media-lakehouse-test", Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                time.sleep(0.1)
                continue
            raise
        return ResultEnvelope.model_validate_json(response["Body"].read())
    raise AssertionError("worker did not commit a result marker")


def _subtitle() -> str:
    lines = []
    for index, text in enumerate(
        (
            "これは日本語の字幕です。",
            "今日もいい天気ですね。",
            "一緒に学校へ行きましょう。",
            "明日の予定を教えてください。",
            "本当にありがとうございました。",
        ),
        start=1,
    ):
        lines.extend(
            (str(index), "00:00:00,000 --> 00:00:01,000", text, "")
        )
    return "\n".join(lines)
