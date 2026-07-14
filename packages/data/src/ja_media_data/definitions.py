"""Loadable Dagster code location for the ja-media data package."""

import dagster as dg

from ja_media_data.bronze import bronze_capture, bronze_scan_sensor


defs = dg.Definitions(
    assets=[bronze_capture],
    sensors=[bronze_scan_sensor],
)
