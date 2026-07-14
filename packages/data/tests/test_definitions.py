"""Definition-shape tests catch Dagster API drift without requiring Garage."""

import dagster as dg

from ja_media_data.definitions import defs


def test_definitions_load() -> None:
    asset_graph = defs.resolve_asset_graph()
    asset_keys = {key.to_user_string() for key in asset_graph.get_all_asset_keys()}
    assert "bronze_capture" in asset_keys
    assert not asset_graph.get(dg.AssetKey("bronze_capture")).is_executable
    sensor = next(sensor for sensor in defs.sensors if sensor.name == "bronze_scan_sensor")
    assert not sensor.has_jobs
