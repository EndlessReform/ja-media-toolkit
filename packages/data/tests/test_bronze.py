"""Tests for the external bronze asset and its reported materializations."""

import dagster as dg
import ja_media_data.bronze as bronze_module

from ja_media_data.bronze import (
    BRONZE_CAPTURE_PARTITIONS,
    _external_materialization,
    bronze_capture,
    bronze_scan_sensor,
)
from ja_media_data.bronze_store import BronzeMarker


def test_bronze_capture_is_an_external_partitioned_asset() -> None:
    assert isinstance(bronze_capture, dg.AssetSpec)
    assert bronze_capture.key == dg.AssetKey("bronze_capture")
    assert bronze_capture.partitions_def is BRONZE_CAPTURE_PARTITIONS


def test_external_materialization_preserves_partition_and_data_version() -> None:
    marker = BronzeMarker(
        capture_id="capture-01J",
        key="audio/anime/bronze/15451/metadata/episode-03.json",
        etag="manifest-etag",
        size=512,
        last_modified="2026-07-13T12:00:00+00:00",
    )

    event = _external_materialization(
        marker,
        {
            "schema_version": 2,
            "capture_id": "capture-01J",
            "series": {"namespace": "anilist", "id": "15451"},
            "subtitles": [{"track_id": "track-01J"}],
        },
    )

    assert event.asset_key == dg.AssetKey("bronze_capture")
    assert event.partition == "capture-01J"
    assert event.tags == {"dagster/data_version": "manifest-etag"}
    assert event.metadata["subtitle_tracks"].value == 1


def test_external_materialization_rejects_capture_id_mismatch() -> None:
    marker = BronzeMarker(
        capture_id="capture-discovered",
        key="audio/anime/bronze/15451/metadata/episode-03.json",
        etag="manifest-etag",
        size=512,
        last_modified="2026-07-13T12:00:00+00:00",
    )

    try:
        _external_materialization(marker, {"capture_id": "capture-other"})
    except ValueError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("capture ID mismatch was accepted")


def test_sensor_registers_partition_and_reports_event(monkeypatch) -> None:
    marker = BronzeMarker(
        capture_id="capture-01J",
        key="audio/anime/bronze/15451/metadata/episode-03.json",
        etag="manifest-etag",
        size=512,
        last_modified="2026-07-13T12:00:00+00:00",
    )

    class FakeStore:
        bucket = "media"

        def scan(self, cached):
            assert cached == {}
            return iter([marker])

        def read_manifest(self, key, *, expected_etag=None):
            assert key == marker.key
            assert expected_etag == marker.etag
            return {
                "schema_version": 2,
                "capture_id": marker.capture_id,
                "series": {"namespace": "anilist", "id": "15451"},
                "source_hint": "episode-03.mkv",
                "audio": {
                    "key": "episode-03.flac",
                    "stream_index": 1,
                    "codec": "flac",
                },
                "subtitles": [],
            }

    class FakeLedger:
        observations = []

        def observe_capture(self, observation):
            self.observations.append(observation)

    monkeypatch.setattr(bronze_module, "bronze_store_from_env", FakeStore)
    ledger = FakeLedger()
    monkeypatch.setattr(bronze_module, "ledger_repository_from_env", lambda: ledger)
    instance = dg.DagsterInstance.ephemeral()
    context = dg.build_sensor_context(instance=instance)

    result = bronze_scan_sensor(context)

    assert result.run_requests == []
    assert [event.partition for event in result.asset_events] == ["capture-01J"]
    assert len(result.dynamic_partitions_requests) == 1
    assert result.dynamic_partitions_requests[0].partition_keys == ["capture-01J"]
    assert [item.capture_id for item in ledger.observations] == ["capture-01J"]
    assert ledger.observations[0].manifest_bucket == "media"
