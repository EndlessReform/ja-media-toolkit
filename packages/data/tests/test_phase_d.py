"""Vertical-slice tests for acceptance, canonicalization, and subtitle LID."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from types import SimpleNamespace
import uuid

import pytest

from ja_media_data.lakehouse import CatalogConfig, apply_schema, connect_catalog
from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.operator.application import OperatorApplication
from ja_media_data.phase_d import (
    compile_acceptances,
    compile_canonical_inputs,
    compile_subtitle_lid,
    run_phase_d,
)
from ja_media_data.resolution_types import (
    BindingProposal,
    CaptureObservation,
    ResolutionBatch,
)


class FakeStore:
    bucket = "bronze"

    def __init__(self) -> None:
        self.fail_reads = False
        self.manifests = {
            "audio/anime/bronze/15451/metadata/old.json": _manifest("old.srt"),
            "audio/anime/bronze/15451/metadata/new.json": _manifest("new.srt"),
        }
        self.text = {
            "audio/anime/bronze/15451/subs/new/new.srt": _japanese_srt()
        }

    def read_manifest(self, key: str, *, expected_etag: str | None = None):
        assert expected_etag in {"etag-old", "etag-new"}
        return self.manifests[key]

    def read_text(self, key: str) -> str:
        if self.fail_reads:
            raise RuntimeError("simulated object read failure")
        return self.text[key]


@pytest.fixture
def repository(tmp_path):
    postgres_url = os.environ.get(
        "JA_MEDIA_PHASE_B_TEST_DATABASE_URL",
        "postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test"
        "@127.0.0.1:55432/ja_media_lakehouse_test",
    )
    connection = connect_catalog(
        CatalogConfig(
            postgres_url=postgres_url,
            metadata_schema="phase_d_" + uuid.uuid4().hex,
            data_path=str(tmp_path / "ducklake"),
        )
    )
    apply_schema(connection)
    yield DuckLakeRepository(connection)
    connection.close()


def test_latest_accepted_capture_reaches_subtitle_lid(repository) -> None:
    _seed_competing_proposals(repository)
    store = FakeStore()

    accepted = compile_acceptances(repository)
    canonical = compile_canonical_inputs(repository, store)
    lid = compile_subtitle_lid(repository, store)

    assert accepted.rows == 2
    assert canonical.rows == 1
    assert repository.connection.execute(
        "SELECT audio_capture_id FROM canonical_episode_inputs"
    ).fetchone() == ("capture-new",)
    assert repository.connection.execute(
        "SELECT object_key FROM canonical_subtitle_inputs"
    ).fetchone() == ("audio/anime/bronze/15451/subs/new/new.srt",)
    assert lid.rows == 1
    assert repository.connection.execute(
        "SELECT language FROM subtitle_language_results"
    ).fetchone() == ("japanese",)

    assert compile_acceptances(repository).written is False
    assert compile_canonical_inputs(repository, store).written is False
    assert compile_subtitle_lid(repository, store).written is False


def test_failed_lid_preserves_previous_complete_product(repository) -> None:
    _seed_competing_proposals(repository)
    store = FakeStore()
    compile_acceptances(repository)
    compile_canonical_inputs(repository, store)
    compile_subtitle_lid(repository, store)
    previous = repository.connection.execute(
        "SELECT subtitle_input_id, language FROM subtitle_language_results"
    ).fetchall()

    repository.connection.execute(
        "UPDATE canonical_subtitle_inputs SET input_fingerprint = 'changed'"
    )
    store.fail_reads = True
    with pytest.raises(RuntimeError, match="simulated object read failure"):
        compile_subtitle_lid(repository, store, force=True)

    assert repository.connection.execute(
        "SELECT subtitle_input_id, language FROM subtitle_language_results"
    ).fetchall() == previous
    assert repository.connection.execute(
        """SELECT disposition FROM run_stage_checkpoints
           ORDER BY started_at DESC LIMIT 1"""
    ).fetchone() == ("failed",)


def test_override_masks_latest_policy_and_unbind_removes_locator(repository) -> None:
    _seed_competing_proposals(repository)
    store = FakeStore()
    compile_acceptances(repository)
    repository.override_repository = _Overrides("capture-old")

    compile_canonical_inputs(repository, store)
    assert repository.connection.execute(
        "SELECT audio_capture_id, binding_source FROM canonical_episode_inputs"
    ).fetchone() == ("capture-old", "override")

    repository.override_repository = _Overrides(None)
    result = compile_canonical_inputs(repository, store)
    assert result.rows == 0
    assert repository.connection.execute(
        "SELECT count(*) FROM canonical_episode_inputs"
    ).fetchone() == (0,)


def test_global_run_keeps_successful_checkpoint_when_downstream_fails(repository) -> None:
    _seed_competing_proposals(repository)
    store = FakeStore()
    initial = run_phase_d(repository, store)
    initial_run = initial[0].pipeline_run_id
    old_lid = initial[2].materialization_id

    repository.override_repository = _Overrides("capture-old")
    store.fail_reads = True
    with pytest.raises(RuntimeError, match="simulated object read failure"):
        run_phase_d(repository, store)

    failed = repository.connection.execute(
        """SELECT run_id, status FROM pipeline_runs
           ORDER BY started_at DESC LIMIT 1"""
    ).fetchone()
    assert repository.connection.execute(
        "SELECT run_number FROM pipeline_runs ORDER BY run_number"
    ).fetchall() == [(1,), (2,)]
    checkpoints = repository.connection.execute(
        """SELECT stage, disposition FROM run_stage_checkpoints
           WHERE run_id = ? ORDER BY ordinal""",
        [failed[0]],
    ).fetchall()
    assert failed[1] == "failed"
    assert checkpoints == [
        ("accepted_bindings", "reused"),
        ("canonical_inputs", "succeeded"),
        ("subtitle_lid", "failed"),
    ]
    assert repository.connection.execute(
        """SELECT materialization_id FROM materializations
           WHERE target = 'subtitle_lid'
           ORDER BY computed_at DESC, materialization_id DESC LIMIT 1"""
    ).fetchone() == (old_lid,)
    assert repository.connection.execute(
        "SELECT status, terminal_snapshot_id FROM pipeline_runs WHERE run_id = ?",
        [initial_run],
    ).fetchone()[0] == "succeeded"
    application = OperatorApplication(repository)
    assert application.get_campaign_snapshot(
        "canonicalization-gate"
    ).lens.gates[0].selected_capture_id == "capture-old"
    historical = application.get_campaign_snapshot(
        "canonicalization-gate", run_id=initial_run
    )
    assert historical.lens.view_run_id == initial_run
    assert historical.lens.gates[0].selected_capture_id == "capture-new"


class _Overrides:
    def __init__(self, capture_id: str | None) -> None:
        self.capture_id = capture_id

    def iter_current_overrides(self):
        yield SimpleNamespace(
            namespace="anilist",
            series_id="15451",
            episode="3",
            audio_capture_id=self.capture_id,
            override_id="override-1",
        )


def _seed_competing_proposals(repository: DuckLakeRepository) -> None:
    captures = (
        ("capture-old", "old", "etag-old", datetime(2026, 1, 1, tzinfo=UTC)),
        ("capture-new", "new", "etag-new", datetime(2026, 2, 1, tzinfo=UTC)),
    )
    repository.replace_bronze_captures(
        [
            CaptureObservation(
                capture_id=capture_id,
                series_namespace="anilist",
                series_id="15451",
                manifest_bucket="bronze",
                manifest_key=f"audio/anime/bronze/15451/metadata/{stem}.json",
                manifest_etag=etag,
                manifest_schema_version=2,
                manifest_modified_at=modified,
                observed_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
            for capture_id, stem, etag, modified in captures
        ],
        "bronze-v1",
    )
    repository.replace_resolution_tables(
        ResolutionBatch(
            hints=(),
            proposals=tuple(
                BindingProposal(
                    proposal_id=f"proposal-{capture_id}",
                    namespace="anilist",
                    series_id="15451",
                    episode="3",
                    audio_capture_id=capture_id,
                    proposal_method="resolver",
                    proposal_evidence={"fixture": True},
                    input_data_version=etag,
                    recipe_version="resolver-v1",
                )
                for capture_id, _, etag, _ in captures
            ),
            issues=(),
        ),
        "proposals-v1",
    )


def _manifest(filename: str) -> dict[str, object]:
    return {
        "subtitles": [
            {
                "filename": filename,
                "stream_index": 2,
                "codec": "srt",
                "declared_language": "jpn",
            }
        ]
    }


def _japanese_srt() -> str:
    lines = [
        "これは日本語の字幕です。今日も一緒に楽しく勉強しましょう。"
        for _ in range(6)
    ]
    return "\n\n".join(
        f"{index}\n00:00:0{index},000 --> 00:00:0{index},900\n{text}"
        for index, text in enumerate(lines, start=1)
    )
