"""Disposable object contracts for the E1-B delayed-worker spike.

This module is intentionally not a general media-product API.  It gives the
three proof steps one small, durable boundary: JSON manifests and media objects
in the local MinIO bucket.  Celery carries execution messages, never audio.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import boto3
from botocore.config import Config


SELECTION_KEY = "phase-e1b/source/current.json"
RECIPE_VERSION = "apple-vad-split-v1"
MODEL_ID = "mlx-community/silero-vad"
CODE_VERSION = "phase-e1b-v1"


class SpikeStore:
    """Read and write only the disposable E1-B object namespace."""

    def __init__(self) -> None:
        self.bucket = os.environ.get(
            "JA_MEDIA_E1B_BUCKET", "ja-media-lakehouse-test"
        )
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ.get(
                "JA_MEDIA_E1B_S3_ENDPOINT_URL", "http://127.0.0.1:59000"
            ),
            aws_access_key_id=os.environ.get(
                "JA_MEDIA_E1B_S3_ACCESS_KEY_ID", "ja_media_lakehouse_test"
            ),
            aws_secret_access_key=os.environ.get(
                "JA_MEDIA_E1B_S3_SECRET_ACCESS_KEY",
                "ja-media-lakehouse-test-only",
            ),
            region_name="us-east-1",
            config=Config(
                s3={"addressing_style": "path"},
                response_checksum_validation="when_required",
            ),
        )

    def read_json(self, key: str) -> dict[str, Any]:
        self._require_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        value = json.loads(response["Body"].read())
        if not isinstance(value, dict):
            raise ValueError(f"E1-B object is not a JSON object: {key}")
        return value

    def put_json(self, key: str, value: Mapping[str, object]) -> None:
        self._require_key(key)
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )

    def upload_file(self, key: str, path: Path) -> dict[str, object]:
        self._require_key(key)
        with path.open("rb") as stream:
            self.client.upload_fileobj(stream, self.bucket, key)
        head = self.head(key)
        return {"key": key, "bytes": head["bytes"], "etag": head["etag"]}

    def upload_stream(self, key: str, stream: BinaryIO) -> dict[str, object]:
        self._require_key(key)
        self.client.upload_fileobj(stream, self.bucket, key)
        head = self.head(key)
        return {"key": key, "bytes": head["bytes"], "etag": head["etag"]}

    def download_file(self, key: str, path: Path) -> None:
        self._require_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(path))

    def head(self, key: str) -> dict[str, object]:
        self._require_key(key)
        response = self.client.head_object(Bucket=self.bucket, Key=key)
        return {
            "bytes": int(response["ContentLength"]),
            "etag": str(response.get("ETag", "")).strip('"'),
        }

    @staticmethod
    def _require_key(key: str) -> None:
        if not key.startswith("phase-e1b/") or ".." in PurePosixPath(key).parts:
            raise ValueError(f"object is outside the E1-B prefix: {key}")


def output_fingerprint(selection_fingerprint: str) -> str:
    """Identify one retry-safe VAD product from immutable inputs and recipe."""

    payload = "\0".join(
        (selection_fingerprint, RECIPE_VERSION, MODEL_ID, CODE_VERSION)
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def output_manifest_key(selection_fingerprint: str) -> str:
    return f"phase-e1b/vad/{output_fingerprint(selection_fingerprint)}/manifest.json"


def selection_entries(value: Mapping[str, object]) -> list[dict[str, Any]]:
    """Validate the bounded source-manifest shape shared by all three steps."""

    if value.get("schema_version") != 1:
        raise ValueError("unsupported E1-B selection schema")
    entries = value.get("episodes")
    if not isinstance(entries, list) or not 3 <= len(entries) <= 5:
        raise ValueError("E1-B selection must contain three to five episodes")
    if not all(isinstance(item, dict) for item in entries):
        raise ValueError("E1-B episodes must be objects")
    locators = [str(item.get("locator", "")) for item in entries]
    if len(set(locators)) != len(locators) or any(not item for item in locators):
        raise ValueError("E1-B locators must be non-empty and unique")
    return entries
