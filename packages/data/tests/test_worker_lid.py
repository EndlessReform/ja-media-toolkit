"""Native subtitle-LID contract, profile, and marker-reuse tests."""

from __future__ import annotations

from datetime import UTC, datetime

from ja_media_data.workers.contracts import (
    ObjectRef,
    ResultEnvelope,
    SubtitleLidRequest,
    WorkEnvelope,
)
from ja_media_data.workers.lid_item import execute_lid_item
from ja_media_data.workers.settings import load_worker_settings
from ja_media_data.products.materialization import MaterializationContext
from ja_media_data.products.subtitle_lid.compiler import CompiledSubtitleLid
from ja_media_data.products.subtitle_lid.models import SubtitleLanguageResult
from ja_media_data.products.subtitle_lid.repository import (
    merge_product,
    replace_product,
)


class FakeBronze:
    bucket = "bronze"

    def __init__(self) -> None:
        self.reads = 0

    def read_text(self, key: str, *, expected_etag: str | None = None) -> str:
        assert key == "audio/anime/bronze/1/subs/episode.srt"
        assert expected_etag == "subtitle-etag"
        self.reads += 1
        return "\n".join(
            [
                "1",
                "00:00:00,000 --> 00:00:01,000",
                "これは日本語の字幕です。",
                "2",
                "00:00:01,000 --> 00:00:02,000",
                "今日もいい天気ですね。",
                "3",
                "00:00:02,000 --> 00:00:03,000",
                "一緒に学校へ行きましょう。",
                "4",
                "00:00:03,000 --> 00:00:04,000",
                "明日の予定を教えてください。",
                "5",
                "00:00:04,000 --> 00:00:05,000",
                "本当にありがとうございました。",
            ]
        )


class FakeMarkers:
    bucket = "staging"
    prefix = "worker-staging/dev"

    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def key_for(self, request_id: str) -> str:
        return f"{self.prefix}/results/{request_id}.json"

    def read_text(self, key: str) -> str | None:
        return self.items.get(key)

    def write_text(self, key: str, body: str) -> None:
        self.items[key] = body


def test_lid_item_writes_and_reuses_one_strict_marker() -> None:
    bronze = FakeBronze()
    markers = FakeMarkers()
    envelope = _envelope()

    first = execute_lid_item(envelope, bronze=bronze, markers=markers)
    repeated = execute_lid_item(envelope, bronze=bronze, markers=markers)

    assert first == repeated
    assert bronze.reads == 1
    assert first.result.script_metrics.japanese_script_characters > 0
    assert ResultEnvelope.model_validate_json(
        next(iter(markers.items.values()))
    ) == first


def test_worker_profile_composes_only_documented_files(tmp_path) -> None:
    profiles = tmp_path / "profiles.toml"
    config = tmp_path / "config.local.toml"
    local_env = tmp_path / ".env.local"
    worker_env = tmp_path / ".env.worker.dev"
    profiles.write_text(
        """[profiles.local-cpu]
broker_host = "broker"
broker_port = 5672
broker_user = "worker"
broker_vhost = "dev"
queue = "cpu-light"
operations = ["subtitle_language_id"]
concurrency = 1
staging_bucket = "staging"
staging_prefix = "worker-staging/dev"
staging_region = "garage"
"""
    )
    config.write_text(
        """[bronze]
endpoint_url = "http://garage"
bucket = "bronze"
prefix = "audio/anime/bronze"
addressing_style = "path"
"""
    )
    local_env.write_text(
        "JA_MEDIA_BRONZE__ACCESS_KEY_ID=bronze-key\n"
        "JA_MEDIA_BRONZE__SECRET_ACCESS_KEY=bronze-secret\n"
    )
    worker_env.write_text(
        "RABBITMQ_PASSWORD=broker-secret\n"
        "JA_MEDIA_WORKER_STAGING_ACCESS_KEY_ID=staging-key\n"
        "JA_MEDIA_WORKER_STAGING_SECRET_ACCESS_KEY=staging-secret\n"
    )

    settings = load_worker_settings(
        "local-cpu",
        profiles_path=profiles,
        local_config=config,
        local_env=local_env,
        worker_env=worker_env,
    )

    assert settings.profile.queue == "cpu-light"
    assert settings.profile.staging_region == "garage"
    assert settings.bronze_bucket == "bronze"
    assert settings.loaded_files == (config, local_env, worker_env)
    assert settings.broker_url == (
        "pyamqp://worker:broker-secret@broker:5672/dev"
    )


def test_lid_merge_preserves_unselected_item_heads(repository) -> None:
    initial = CompiledSubtitleLid(
        (_row("subtitle-1", "japanese"), _row("subtitle-2", "unknown")),
        "initial",
    )
    replace_product(repository.connection, initial, _context("initial"))

    changed = CompiledSubtitleLid(
        (_row("subtitle-1", "bilingual"),),
        "changed-subtitle-1",
    )
    committed = merge_product(
        repository.connection, changed, _context("changed-subtitle-1")
    )

    assert committed.rows == 1
    assert repository.connection.execute(
        """SELECT subtitle_input_id, language
           FROM subtitle_language_results ORDER BY subtitle_input_id"""
    ).fetchall() == [
        ("subtitle-1", "bilingual"),
        ("subtitle-2", "unknown"),
    ]


def _envelope() -> WorkEnvelope:
    return WorkEnvelope(
        request_id="lid-request-1",
        campaign_run_id="run-1",
        step_key="subtitle_lid",
        attempt=1,
        requested_at=datetime.now(UTC),
        payload=SubtitleLidRequest(
            subtitle_input_id="subtitle-1",
            source=ObjectRef(
                bucket="bronze",
                key="audio/anime/bronze/1/subs/episode.srt",
                etag="subtitle-etag",
            ),
            codec="srt",
            input_fingerprint="input-1",
            recipe_revision="subtitle-script-fasttext-v1",
            staging_bucket="staging",
            staging_prefix="worker-staging/dev",
        ),
    )


def _row(subtitle_id: str, language: str) -> SubtitleLanguageResult:
    return SubtitleLanguageResult(
        subtitle_input_id=subtitle_id,
        namespace="anilist",
        series_id="1",
        episode="1",
        audio_capture_id="capture-1",
        language=language,
        reason="fixture",
        script_metrics={"visible_characters": 100},
        sampled_metrics=None,
        input_fingerprint=f"input-{subtitle_id}",
        recipe_version="subtitle-script-fasttext-v1",
    )


def _context(label: str) -> MaterializationContext:
    return MaterializationContext(
        attempt_id=f"test:{label}",
        pipeline_run_id="test-run",
        recipe_revision="subtitle-script-fasttext-v1",
        build_key=label,
        input_heads={"canonical_inputs": {"fingerprint": "canonical"}},
    )
