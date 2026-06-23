"""Small operator smoke check for the anime-audio service."""

from __future__ import annotations

import argparse
import json
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description="Check anime-audio service health")
    parser.add_argument("--url", default="http://127.0.0.1:8000/healthz")
    args = parser.parse_args()
    with urllib.request.urlopen(args.url, timeout=10) as response:
        payload = json.load(response)
    if payload.get("status") not in {"ok", "degraded"}:
        raise SystemExit(f"anime-audio service is not ready: {payload}")
