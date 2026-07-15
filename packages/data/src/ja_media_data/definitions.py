"""Loadable Dagster code location for the ja-media data package."""

import dagster as dg

from ja_media_data.bronze import bronze_capture, bronze_scan_sensor
from ja_media_data.episode_assets import (
    episode_resolution,
    stable_episode_mapping,
    validated_episode_mapping,
)


defs = dg.Definitions(
    assets=[bronze_capture, episode_resolution, validated_episode_mapping],
    asset_checks=[stable_episode_mapping],
    sensors=[bronze_scan_sensor],
)
