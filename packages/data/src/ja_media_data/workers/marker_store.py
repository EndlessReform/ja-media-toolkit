"""Small S3 adapter for temporary worker result markers."""

from __future__ import annotations

import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class MarkerStore:
    """Read and write markers inside one configured staging prefix."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        prefix: str,
        region: str,
        addressing_style: str,
        access_key_id: str,
        secret_access_key: str,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                s3={"addressing_style": addressing_style},
                response_checksum_validation="when_required",
                request_checksum_calculation="when_required",
            ),
        )

    def key_for(self, request_id: str) -> str:
        """Return the only marker key one request may write."""

        return f"{self.prefix}/results/{request_id}.json"

    def read_text(self, key: str) -> str | None:
        """Return a marker body, or None when it does not exist."""

        self._validate_key(key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in {
                "NoSuchKey",
                "404",
            }:
                return None
            raise
        return response["Body"].read().decode()

    def write_text(self, key: str, body: str) -> None:
        """Write one deterministic JSON marker."""

        self._validate_key(key)
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body.encode(),
            ContentType="application/json",
        )

    def probe(self) -> None:
        """Prove bounded write/read/delete access under the doctor prefix."""

        self.probe_bucket()
        key = f"{self.prefix}/doctor/{uuid.uuid4().hex}.json"
        body = '{"doctor":"ok"}'
        self.write_text(key, body)
        try:
            if self.read_text(key) != body:
                raise RuntimeError("staging probe returned unexpected content")
        finally:
            self._client.delete_object(Bucket=self.bucket, Key=key)

    def probe_bucket(self) -> None:
        """Confirm the configured staging bucket is reachable."""

        self._client.head_bucket(Bucket=self.bucket)

    def _validate_key(self, key: str) -> None:
        if not key.startswith(self.prefix + "/"):
            raise ValueError(f"marker key is outside staging prefix: {key}")
