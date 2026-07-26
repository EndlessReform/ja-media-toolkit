"""Read-only discovery of committed bronze captures in S3-compatible storage.

Bronze metadata objects are commit markers: audio or subtitle objects without a
matching manifest are deliberately invisible to orchestration.  The adapter is
small so Garage access and pagination policy do not leak into asset functions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterator

import boto3
from botocore.config import Config

from ja_media_data.settings import DataSettings, get_settings


@dataclass(frozen=True)
class BronzeMarker:
    """A committed bronze manifest and the S3 token observed during a scan."""

    capture_id: str
    key: str
    etag: str
    size: int
    last_modified: str


@dataclass(frozen=True)
class BronzeDocument:
    """A marker and manifest read together for bounded analytical batches."""

    marker: BronzeMarker
    manifest: dict[str, Any]


class BronzeStore:
    """List and read bronze commit markers through the S3 API."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        prefix: str,
        addressing_style: str,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/"
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                s3={"addressing_style": addressing_style},
                response_checksum_validation="when_required",
            ),
        )

    def scan(
        self, cached: Mapping[str, tuple[str, str]] | None = None
    ) -> Iterator[BronzeMarker]:
        """Yield commit markers, reusing IDs for unchanged key/ETag pairs.

        Legacy manifests require a read to derive their deterministic capture
        ID. The sensor persists this cache so routine repair scans remain one
        paginated listing rather than thousands of object reads.
        """

        cached = cached or {}
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                if not _is_manifest_key(key):
                    continue
                etag = item.get("ETag", "").strip('"')
                cached_marker = cached.get(key)
                capture_id = (
                    cached_marker[1]
                    if cached_marker and cached_marker[0] == etag
                    else _capture_id(
                        self.read_manifest(key, expected_etag=etag), self.bucket, key
                    )
                )
                yield BronzeMarker(
                    capture_id=capture_id,
                    key=key,
                    etag=etag,
                    size=item["Size"],
                    last_modified=item["LastModified"].isoformat(),
                )

    def probe(self) -> None:
        """Confirm bounded list access without reading a bronze object body."""

        self._client.list_objects_v2(
            Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1
        )

    def read_manifest(
        self, key: str, *, expected_etag: str | None = None
    ) -> dict[str, Any]:
        """Read one manifest and optionally require the listed object version.

        The repair scan uses the listing ETag as the source data version.
        Rejecting a changed object prevents a scan/read race from attaching old
        version metadata to new manifest contents; the next sensor tick retries
        the newly listed version.
        """

        if not key.startswith(self.prefix) or not _is_manifest_key(key):
            raise ValueError(f"not a bronze manifest key: {key}")
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        observed_etag = response.get("ETag", "").strip('"')
        if expected_etag is not None and observed_etag != expected_etag:
            raise RuntimeError(
                f"manifest changed during scan: expected ETag {expected_etag!r}, "
                f"read {observed_etag!r}"
            )
        return json.loads(response["Body"].read())

    def read_text(self, key: str, *, expected_etag: str | None = None) -> str:
        """Read one committed text object and optionally require its S3 token."""

        if not key.startswith(self.prefix):
            raise ValueError(f"object is outside the bronze prefix: {key}")
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        observed_etag = response.get("ETag", "").strip('"')
        if expected_etag is not None and observed_etag != expected_etag:
            raise RuntimeError(
                f"subtitle changed: expected ETag {expected_etag!r}, "
                f"read {observed_etag!r}"
            )
        return response["Body"].read().decode("utf-8-sig")

    def scan_documents(self, *, limit: int | None) -> Iterator[BronzeDocument]:
        """Read each selected marker once, including legacy IDs.

        The repair sensor's cursor makes its marker-only scan efficient after
        bootstrap.  A one-shot resolver has no cursor, so this separate path
        avoids reading every legacy manifest once for identity and again for
        its contents.

        ``None`` selects the complete collection for a corpus materialization.
        A numeric limit is reserved for non-publishing canaries and explicitly
        isolated catalogs; callers must not present it as a corpus head.
        """

        if limit is not None and limit < 1:
            return
        paginator = self._client.get_paginator("list_objects_v2")
        yielded = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                if not _is_manifest_key(key):
                    continue
                etag = item.get("ETag", "").strip('"')
                manifest = self.read_manifest(key, expected_etag=etag)
                marker = BronzeMarker(
                    capture_id=_capture_id(manifest, self.bucket, key),
                    key=key,
                    etag=etag,
                    size=item["Size"],
                    last_modified=item["LastModified"].isoformat(),
                )
                yield BronzeDocument(marker=marker, manifest=manifest)
                yielded += 1
                if limit is not None and yielded >= limit:
                    return


def bronze_store_from_settings(settings: DataSettings | None = None) -> BronzeStore:
    """Build the read-only Garage adapter from validated deployment settings."""

    configured = (settings or get_settings()).bronze
    return BronzeStore(
        endpoint_url=configured.endpoint_url,
        bucket=configured.bucket,
        prefix=configured.prefix,
        addressing_style=configured.addressing_style,
        access_key_id=configured.access_key_id,
        secret_access_key=configured.secret_access_key,
    )


def _is_manifest_key(key: str) -> bool:
    parts = key.split("/")
    return len(parts) >= 3 and parts[-2] == "metadata" and parts[-1].endswith(".json")


def _capture_id(manifest: dict[str, Any], bucket: str, key: str) -> str:
    capture_id = manifest.get("capture_id")
    if isinstance(capture_id, str) and capture_id.strip():
        return capture_id.strip()
    digest = hashlib.sha256(f"{bucket}:{key}".encode()).hexdigest()[:26]
    return f"capture-{digest}"
