"""Shared real-table setup for operator integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from ja_media_data.lakehouse.repository import DuckLakeRepository
from ja_media_data.phase_d import compile_acceptances, compile_canonical_inputs
from ja_media_data.resolution_types import (
    BindingProposal,
    CaptureObservation,
    ResolutionBatch,
)


class ManifestStore:
    """Small bronze reader sufficient for canonical input compilation."""

    bucket = "bronze"

    def read_manifest(self, key: str, *, expected_etag: str | None = None):
        assert key.endswith(("old.json", "new.json"))
        assert expected_etag in {"etag-old", "etag-new"}
        return {"subtitles": []}


class Overrides:
    def __init__(self, capture_id: str | None) -> None:
        self.capture_id = capture_id

    def iter_current_overrides(self):
        yield SimpleNamespace(
            namespace="anilist", series_id="15451", episode="3",
            audio_capture_id=self.capture_id, override_id="override-1",
        )


def compile_campaign(repository: DuckLakeRepository) -> None:
    """Commit two competing real proposals and their canonical product."""

    captures = (
        ("capture-old", "old", "etag-old", datetime(2026, 1, 1, tzinfo=UTC)),
        ("capture-new", "new", "etag-new", datetime(2026, 2, 1, tzinfo=UTC)),
    )
    repository.replace_bronze_captures([
        CaptureObservation(
            capture_id=capture_id, series_namespace="anilist", series_id="15451",
            manifest_bucket="bronze",
            manifest_key=f"audio/anime/bronze/15451/metadata/{stem}.json",
            manifest_etag=etag, manifest_schema_version=2,
            manifest_modified_at=modified,
            observed_at=datetime(2026, 3, 1, tzinfo=UTC),
        ) for capture_id, stem, etag, modified in captures
    ], "bronze-v1")
    repository.replace_resolution_tables(ResolutionBatch(
        hints=(), issues=(), proposals=tuple(BindingProposal(
            proposal_id=f"proposal-{capture_id}", namespace="anilist",
            series_id="15451", episode="3", audio_capture_id=capture_id,
            proposal_method="resolver", proposal_evidence={"fixture": True},
            input_data_version=etag, recipe_version="resolver-v1",
        ) for capture_id, _, etag, _ in captures),
    ), "proposals-v1")
    compile_acceptances(repository)
    compile_canonical_inputs(repository, ManifestStore())
