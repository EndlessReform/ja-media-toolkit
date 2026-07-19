---
title: Lakehouse operations
description: Back up and verify the DuckLake catalog used by the media data lake.
---

DuckLake stores derived table data as Parquet in Garage, but PostgreSQL remains
load-bearing: its catalog records which files belong to every snapshot, and the
separate `ja_media_control.binding_overrides` table owns human episode-binding
decisions. Garage objects alone cannot reconstruct either history. Back up both
PostgreSQL schemas before DuckLake maintenance that expires snapshots, compacts
files, or removes old objects.

## Apply the checked-in schema

The schema entrypoint attaches the PostgreSQL-backed DuckLake and applies both
sets of numbered SQL files: DuckLake relations from `packages/data/schema/`
and the ordinary PostgreSQL decision table from
`packages/data/postgres_schema/`. Each schema records SHA-256 checksums;
re-running is a no-op, and editing an applied file is rejected.

```sh
cd packages/data
uv run ja-data apply-lakehouse-schema
```

`ja-data` merges `.env` files from the Git repository root through its
invocation directory, with more specific files winning. Already-exported
process variables remain authoritative, and discovery never crosses the
repository boundary.

Configure `JA_MEDIA_DUCKLAKE_CATALOG_SCHEMA` and either a direct
`JA_MEDIA_DUCKLAKE_DATA_PATH` or both `JA_MEDIA_BRONZE_BUCKET` and
`JA_MEDIA_DUCKLAKE_DATA_PREFIX`. An S3 path also requires the purpose-specific
`JA_MEDIA_DUCKLAKE_S3_ENDPOINT_URL`, access-key, secret-key, and optional
region variables. There is deliberately no shared S3 endpoint variable:
bronze and DuckLake may use different authorities. The adapter creates the PostgreSQL metadata schema
when absent; `JA_MEDIA_CONTROL_SCHEMA` selects the separate application-owned
schema for binding decisions. Neither path drops or resets existing state.

DuckLake's automatic resolver tables intentionally contain no key constraints;
their batch writer validates the complete result before replacement. Human
overrides use PostgreSQL partial unique indexes for active locator and capture
heads. The effective binding query takes the active PostgreSQL override first,
including an explicit unbind, and falls back to automatic DuckLake output.

## Record a binding override

The command validates that a capture exists in the DuckLake bronze index, then
advances the PostgreSQL override head in one transaction:

```sh
uv run --directory packages/data \
  ja-data bind anilist 15451 3 --capture capture-id --note "manual correction"

uv run --directory packages/data \
  ja-data bind anilist 15451 3 --unbind --note "not episode 3"
```

PostgreSQL rejects an attempt to make one capture active under two override
locators. A successful decision is visible through repository reads without a
DuckLake rebuild.

## Validate the lakehouse locally

Start the disposable stack described in `deploy/lakehouse-dev/README.md`, then
run the normal PostgreSQL-catalog tests with local table files:

```sh
uv run pytest packages/data/tests/test_lakehouse_catalog.py
```

The object-storage smoke is opt-in and writes only to the disposable MinIO
bucket under `ducklake/tests/<random-id>/`:

```sh
JA_MEDIA_PHASE_B_MINIO_SMOKE=1 \
  uv run pytest packages/data/tests/test_lakehouse_minio.py
```

MinIO proves local S3-compatible wiring. The retained Phase A report records
the already-completed Garage compatibility result.

## Compile real bronze into a local lakehouse

The local development pipeline intentionally uses two S3 configurations in one
process:

- the AWS credential chain plus `JA_MEDIA_BRONZE_S3_*` settings read immutable
  bronze from Garage; and
- `JA_MEDIA_DUCKLAKE_S3_*` overrides write DuckLake table data to local MinIO.

Start the disposable stack, then source the existing Garage environment before
the local destination overlay:

```sh
set -a
source packages/data/.env
source deploy/lakehouse-dev/local-ducklake.env.example
set +a

uv run --directory packages/data ja-data scan-bronze --limit 100
uv run --directory packages/data \
  ja-data resolve-sample --limit 100 --show issues
```

`scan-bronze` reads each committed manifest with ETag verification and replaces
the normalized cache as one compiled product. `resolve-sample` is a
non-publishing canary. Durable resolution, acceptance, and canonicalization run
through the Dagster campaign documented in
[Lakehouse operator workbench](/setup/operator-workbench/); bounded sample
application was removed with the custom executor.

Each product records a content fingerprint and lineage metadata in
`materializations`. Repeating an identical compilation avoids rewriting product
rows while Dagster records the execution. A changed input replaces the product
atomically; the previous version remains available through DuckLake snapshot
time travel.

## Back up the catalog

Load the package environment without printing it, then make a custom-format
dump. Replace the example schema names with the configured environment's
catalog and decision schemas.

```sh
cd packages/data
set -a
source .env
set +a

pg_dump \
  --format=custom \
  --schema=ja_media_ducklake \
  --schema=ja_media_control \
  --file=/path/on-backed-up-storage/ja-media-ducklake-catalog.dump \
  "${JA_MEDIA_DATA_DATABASE_URL/postgresql+psycopg:/postgresql:}"
```

Include both the configured DuckLake catalog and control-plane schema names.
`pg_dump` may not recognize the driver-qualified `postgresql+psycopg://`
scheme. For a manual run, supply the equivalent standard `postgresql://` URL without printing
it or place the connection fields in the usual `PGHOST`, `PGPORT`, `PGDATABASE`,
`PGUSER`, and `PGPASSWORD` variables. The `pg_dump` major version must be at
least as new as the PostgreSQL server. On Homebrew systems, the current client
is normally `/opt/homebrew/opt/libpq/bin/pg_dump`.

The catalog backup cadence defines the recovery-point objective: a dump taken
before a write cannot recover the membership of Parquet files created by that
write. Run a regular catalog backup and always take one before snapshot expiry,
compaction, or file cleanup. Confirm that the infrastructure backup regimen
actually includes both databases/schemas before treating the catalog as
operationally recoverable.

## Restore and verify

Restore into an empty database or empty catalog schema, then attach DuckLake
with the same Garage data path. A successful `pg_restore` is not enough: query a
known table through DuckLake so the test proves that both catalog metadata and
referenced Garage objects are available.

```sh
pg_restore \
  --no-owner \
  --no-privileges \
  --dbname=ja_media_data_dev \
  /path/on-backed-up-storage/ja-media-ducklake-catalog.dump

cd packages/data
set -a
source .env
set +a
uv run ja-data resolution-report --limit 1
```

Do not restore over a populated catalog. Restore into an empty target, verify
it, and only then change clients to use it.
