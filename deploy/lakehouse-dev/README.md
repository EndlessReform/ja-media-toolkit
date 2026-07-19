# Lakehouse development stack

This isolated Compose project provides disposable infrastructure for DuckLake
schema, integration, and control-plane tests:

- PostgreSQL 17 as the DuckLake metadata catalog; and
- MinIO as an optional path-style S3 development surrogate.

It is not part of the shared DEV deployment. Normal tests use PostgreSQL plus a
temporary local filesystem data path; only opt-in storage tests need MinIO.

## Start and inspect

The checked-in defaults are deliberately non-secret and local-only:

```sh
cd deploy/lakehouse-dev
docker compose up -d --wait postgres minio
docker compose up minio-init
docker compose ps
```

`minio-init` is a one-shot bucket initializer. Exit code zero means the bucket
exists and anonymous access is disabled. It is safe to run again.

Use these host-side settings for development commands and integration tests:

```sh
export JA_MEDIA_DATA_DATABASE_URL='postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test@127.0.0.1:55432/ja_media_lakehouse_test'
export JA_MEDIA_DUCKLAKE_CATALOG_SCHEMA='ja_media_ducklake_test'
export JA_MEDIA_DUCKLAKE_DATA_PATH='s3://ja-media-lakehouse-test/ducklake/tests/manual/'
export JA_MEDIA_DUCKLAKE_S3_ENDPOINT_URL='http://127.0.0.1:59000'
export JA_MEDIA_DUCKLAKE_S3_ACCESS_KEY_ID='ja_media_lakehouse_test'
export JA_MEDIA_DUCKLAKE_S3_SECRET_ACCESS_KEY='ja-media-lakehouse-test-only'
export JA_MEDIA_DUCKLAKE_S3_REGION='us-east-1'
```

The normal Phase B suite supplies a temporary local data path itself and uses
`JA_MEDIA_PHASE_B_TEST_DATABASE_URL` only when overriding the default URL
above. Set `JA_MEDIA_PHASE_B_MINIO_SMOKE=1` to run the opt-in MinIO test; it
generates its own isolated catalog schema and object prefix.

The MinIO console is available at `http://127.0.0.1:59001`. It is for local
inspection only and must not be exposed beyond the development machine.

## Compile real bronze into local products

Load the configured read-only Garage settings first, then overlay the checked-in
local destination settings. This keeps standard AWS variables pointed at
Garage for bronze reads while the purpose-specific DuckLake variables point at
MinIO:

```sh
cd /path/to/ja-media-toolkit
set -a
source packages/data/.env
source deploy/lakehouse-dev/local-ducklake.env.example
set +a

uv run --directory packages/data ja-data scan-bronze --limit 100
uv run --directory packages/data \
  ja-data resolve-sample --limit 100 --apply --show none
uv run --directory packages/data ja-data resolution-report --limit 0
```

The resolver replaces all automatic identity tables in one transaction and
flushes inlined rows after a changed batch. Repeating identical inputs is a
fingerprint no-op with zero table writes. The Garage adapter performs only
`LIST` and `GET`; these commands do not write bronze.

## Reset

All state belongs to named volumes under the `ja-media-lakehouse-dev` Compose
project. Remove those volumes for a clean catalog and bucket:

```sh
cd deploy/lakehouse-dev
docker compose down --volumes --remove-orphans
```

Never point this stack's reset workflow at the shared root Compose project or a
remote Docker context.
