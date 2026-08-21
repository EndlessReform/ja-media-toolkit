"""Server-owned selection, dispatch, and collection for subtitle LID items."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import time

from celery import Celery
import duckdb

from ja_media_data.products.identities import fingerprint, stable_id
from ja_media_data.products.subtitle_lid.compiler import (
    CompiledSubtitleLid,
    SUBTITLE_LID_RECIPE_VERSION,
)
from ja_media_data.products.subtitle_lid.models import SubtitleLanguageResult
from ja_media_data.workers.app import TASK_NAME
from ja_media_data.workers.contracts import (
    ObjectRef,
    ResultEnvelope,
    SubtitleLidRequest,
    SubtitleLidResult,
    WorkEnvelope,
)
from ja_media_data.workers.marker_store import MarkerStore


@dataclass(frozen=True)
class LidSource:
    """One frozen canonical subtitle selected for worker execution."""

    subtitle_input_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str
    object_bucket: str
    object_key: str
    codec: str | None
    input_fingerprint: str


@dataclass(frozen=True)
class LidHandoffBatch:
    """Collected product rows plus any items that did not return valid markers."""

    product: CompiledSubtitleLid
    selected: int
    failed_request_ids: tuple[str, ...]


class CeleryLidHandoff:
    """Publish bounded work to RabbitMQ and collect marker-last results."""

    def __init__(
        self,
        *,
        broker_url: str,
        markers: MarkerStore,
        queue: str = "cpu-light",
        timeout_seconds: float = 900,
        poll_seconds: float = 0.25,
    ) -> None:
        self.markers = markers
        self.queue = queue
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._app = Celery("ja_media_lid_dispatch", broker=broker_url)

    def execute(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        campaign_run_id: str,
        step_key: str,
        limit: int,
    ) -> LidHandoffBatch:
        """Freeze eligible rows, publish each once, and collect bounded results."""

        sources = select_eligible(connection, limit=limit)
        pending: dict[str, tuple[LidSource, WorkEnvelope]] = {}
        failed: list[str] = []
        for source in sources:
            envelope = self._envelope(source, campaign_run_id, step_key)
            request_id = envelope.request_id
            self._record_requested(connection, envelope, source)
            try:
                self._app.send_task(
                    TASK_NAME,
                    args=[envelope.model_dump(mode="json")],
                    queue=self.queue,
                )
            except Exception:
                self._record_failed(connection, request_id, campaign_run_id)
                failed.append(request_id)
            else:
                pending[request_id] = (source, envelope)

        rows: list[SubtitleLanguageResult] = []
        deadline = time.monotonic() + self.timeout_seconds
        while pending and time.monotonic() < deadline:
            for request_id, (source, envelope) in tuple(pending.items()):
                marker_key = self.markers.key_for(request_id)
                body = self.markers.read_text(marker_key)
                if body is None:
                    continue
                try:
                    result = _validated_result(body, envelope)
                except Exception:
                    self._record_failed(connection, request_id, campaign_run_id)
                    failed.append(request_id)
                else:
                    rows.append(_product_row(source, result))
                    self._record_succeeded(connection, envelope, result, marker_key)
                del pending[request_id]
            if pending:
                time.sleep(self.poll_seconds)

        for request_id in pending:
            self._record_failed(connection, request_id, campaign_run_id)
            failed.append(request_id)
        product = CompiledSubtitleLid(
            tuple(rows),
            fingerprint(
                SUBTITLE_LID_RECIPE_VERSION,
                [(row.subtitle_input_id, row.input_fingerprint) for row in rows],
            ),
        )
        return LidHandoffBatch(product, len(sources), tuple(sorted(failed)))

    def _envelope(
        self, source: LidSource, campaign_run_id: str, step_key: str
    ) -> WorkEnvelope:
        request_id = stable_id(
            "lid-request",
            campaign_run_id,
            source.subtitle_input_id,
            source.input_fingerprint,
            SUBTITLE_LID_RECIPE_VERSION,
        )
        return WorkEnvelope(
            request_id=request_id,
            campaign_run_id=campaign_run_id,
            step_key=step_key,
            attempt=1,
            requested_at=datetime.now(UTC),
            payload=SubtitleLidRequest(
                subtitle_input_id=source.subtitle_input_id,
                source=ObjectRef(bucket=source.object_bucket, key=source.object_key),
                codec=source.codec,
                input_fingerprint=source.input_fingerprint,
                recipe_revision=SUBTITLE_LID_RECIPE_VERSION,
                staging_bucket=self.markers.bucket,
                staging_prefix=self.markers.prefix,
            ),
        )

    def _record_requested(
        self,
        connection: duckdb.DuckDBPyConnection,
        envelope: WorkEnvelope,
        source: LidSource,
    ) -> None:
        marker_key = self.markers.key_for(envelope.request_id)
        connection.execute(
            """DELETE FROM worker_handoff_items
               WHERE request_id = ? AND campaign_run_id = ?""",
            [envelope.request_id, envelope.campaign_run_id],
        )
        connection.execute(
            """INSERT INTO worker_handoff_items
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'requested', ?, ?, ?, NULL, ?)""",
            [
                envelope.request_id,
                envelope.campaign_run_id,
                envelope.step_key,
                "subtitle_language_id",
                source.subtitle_input_id,
                envelope.contract_version,
                source.input_fingerprint,
                self.markers.bucket,
                marker_key,
                envelope.requested_at,
                datetime.now(UTC),
            ],
        )

    @staticmethod
    def _record_failed(
        connection: duckdb.DuckDBPyConnection,
        request_id: str,
        campaign_run_id: str,
    ) -> None:
        connection.execute(
            """UPDATE worker_handoff_items
               SET disposition = 'failed', completed_at = ?, compacted_at = ?
               WHERE request_id = ? AND campaign_run_id = ?""",
            [datetime.now(UTC), datetime.now(UTC), request_id, campaign_run_id],
        )

    @staticmethod
    def _record_succeeded(
        connection: duckdb.DuckDBPyConnection,
        envelope: WorkEnvelope,
        result: SubtitleLidResult,
        marker_key: str,
    ) -> None:
        connection.execute(
            """UPDATE worker_handoff_items
               SET output_fingerprint = ?, disposition = 'succeeded',
                   result_marker_key = ?, completed_at = ?, compacted_at = ?
               WHERE request_id = ? AND campaign_run_id = ?""",
            [
                result.output_fingerprint,
                marker_key,
                datetime.now(UTC),
                datetime.now(UTC),
                envelope.request_id,
                envelope.campaign_run_id,
            ],
        )


def select_eligible(
    connection: duckdb.DuckDBPyConnection, *, limit: int
) -> tuple[LidSource, ...]:
    """Select canonical subtitles without a current result for this recipe."""

    if limit < 1:
        raise ValueError("subtitle LID limit must be positive")
    rows = connection.execute(
        """SELECT canonical.subtitle_input_id, canonical.namespace,
                  canonical.series_id, canonical.episode,
                  canonical.audio_capture_id, canonical.object_bucket,
                  canonical.object_key, canonical.codec,
                  canonical.input_fingerprint
           FROM canonical_subtitle_inputs AS canonical
           LEFT JOIN subtitle_language_results AS result
             ON result.subtitle_input_id = canonical.subtitle_input_id
            AND result.recipe_version = ?
           WHERE result.subtitle_input_id IS NULL
              OR result.input_fingerprint <> canonical.input_fingerprint
           ORDER BY canonical.subtitle_input_id
           LIMIT ?""",
        [SUBTITLE_LID_RECIPE_VERSION, limit],
    ).fetchall()
    return tuple(LidSource(*row) for row in rows)


def _validated_result(body: str, envelope: WorkEnvelope) -> SubtitleLidResult:
    result = ResultEnvelope.model_validate_json(body)
    item = result.result
    request = envelope.payload
    if not isinstance(item, SubtitleLidResult):
        raise RuntimeError("worker returned another operation")
    if not isinstance(request, SubtitleLidRequest):
        raise RuntimeError("request contains another operation")
    if (
        result.request_id != envelope.request_id
        or item.subtitle_input_id != request.subtitle_input_id
        or item.input_fingerprint != request.input_fingerprint
        or item.recipe_revision != request.recipe_revision
    ):
        raise RuntimeError("worker result does not match request")
    return item


def _product_row(
    source: LidSource, result: SubtitleLidResult
) -> SubtitleLanguageResult:
    return SubtitleLanguageResult(
        subtitle_input_id=source.subtitle_input_id,
        namespace=source.namespace,
        series_id=source.series_id,
        episode=source.episode,
        audio_capture_id=source.audio_capture_id,
        language=result.language.value,
        reason=result.reason,
        script_metrics=asdict(result.script_metrics),
        sampled_metrics=(
            asdict(result.sampled_metrics) if result.sampled_metrics else None
        ),
        input_fingerprint=result.input_fingerprint,
        recipe_version=result.recipe_revision,
    )
