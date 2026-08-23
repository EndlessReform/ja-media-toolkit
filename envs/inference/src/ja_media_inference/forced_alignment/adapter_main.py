"""Uvicorn import target for the colocated forced-alignment adapter."""

from ja_media_inference.forced_alignment.adapter_app import create_configured_app


app = create_configured_app()
