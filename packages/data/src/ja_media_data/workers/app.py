"""Broker-only Celery application for supported native item operations."""

from __future__ import annotations

from celery import Celery

from ja_media_data.storage.bronze import BronzeStore
from ja_media_data.workers.contracts import WorkEnvelope
from ja_media_data.workers.lid_item import execute_lid_item
from ja_media_data.workers.marker_store import MarkerStore
from ja_media_data.workers.settings import WorkerSettings


TASK_NAME = "ja_media.worker.execute"


def build_worker_app(settings: WorkerSettings) -> Celery:
    """Build one least-privilege consumer for the selected profile."""

    app = Celery("ja_media_worker", broker=settings.broker_url)
    app.conf.update(
        task_default_queue=settings.profile.queue,
        task_serializer="json",
        accept_content=["json"],
        result_backend=None,
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
    )
    bronze = BronzeStore(
        endpoint_url=settings.bronze_endpoint_url,
        bucket=settings.bronze_bucket,
        prefix=settings.bronze_prefix,
        addressing_style=settings.bronze_addressing_style,
        access_key_id=settings.secrets.bronze_access_key_id,
        secret_access_key=settings.secrets.bronze_secret_access_key,
    )
    markers = MarkerStore(
        endpoint_url=settings.bronze_endpoint_url,
        bucket=settings.profile.staging_bucket,
        prefix=settings.profile.staging_prefix,
        region=settings.profile.staging_region,
        addressing_style=settings.bronze_addressing_style,
        access_key_id=settings.secrets.staging_access_key_id,
        secret_access_key=settings.secrets.staging_secret_access_key,
    )

    @app.task(name=TASK_NAME, shared=False)
    def execute_item(payload: dict[str, object]) -> None:
        envelope = WorkEnvelope.model_validate(payload)
        if envelope.payload.operation not in settings.profile.operations:
            raise ValueError(
                f"profile {settings.profile.name} does not support "
                f"{envelope.payload.operation}"
            )
        execute_lid_item(envelope, bronze=bronze, markers=markers)

    return app
