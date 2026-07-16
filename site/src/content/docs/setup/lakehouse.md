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
set -a
source .env
set +a
uv run ja-data apply-lakehouse-schema
```

Configure `JA_MEDIA_DUCKLAKE_CATALOG_SCHEMA` and either a direct
`JA_MEDIA_DUCKLAKE_DATA_PATH` or both `JA_MEDIA_BRONZE_BUCKET` and
`JA_MEDIA_DUCKLAKE_DATA_PREFIX`. An S3 path also uses the configured endpoint,
region, and AWS credentials. The adapter creates the PostgreSQL metadata schema
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

MinIO proves local S3-compatible wiring; the Phase A Garage result remains the
evidence for the real object store.

## Compile real bronze into a local lakehouse

The local development pipeline intentionally uses two S3 configurations in one
process:

- standard `AWS_*` and `JA_MEDIA_S3_*` settings read immutable bronze from
  Garage; and
- `JA_MEDIA_DUCKLAKE_S3_*` overrides write DuckLake table data to local MinIO.

Start the disposable stack, then source the existing Garage environment before
the local destination overlay:

```sh
set -a
source packages/data/.env
source deploy/lakehouse-dev/phase-c.env.example
set +a

uv run --directory packages/data ja-data scan-bronze --limit 100
uv run --directory packages/data \
  ja-data resolve-sample --limit 100 --apply --show none
```

`scan-bronze` reads each committed manifest with ETag verification and replaces
the normalized cache as one compiled product. The resolver reads the same
bounded ordering, queries exact AniList metadata through the core SDK, validates
the complete result, and replaces `episode_hints_auto`,
`episode_bindings_auto`, and `resolution_issues_auto` in one transaction.

Each product records a content fingerprint in `materializations`. Repeating an
identical command performs zero table writes. A changed input replaces the
product atomically; the previous version remains available through DuckLake
snapshot time travel.

## Back up the catalog

Load the package environment without printing it, then make a custom-format
dump. Phase A uses a disposable schema inside the development database; replace
the schema name with the production catalog schema when that is introduced.

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
actually includes this database/schema before treating Phase A as operationally
complete.

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
uv run scripts/phase_a_ducklake_spike.py verify
```

Do not restore over a populated catalog. Restore into an empty target, verify
it, and only then change clients to use it.

## Run the Phase A compatibility spike

The spike writes only beneath
`s3://$JA_MEDIA_BRONZE_BUCKET/audio/anime/lakehouse/phase-a/` and destroys only
the PostgreSQL schema `ja_media_ducklake_phase_a`. Both guardrails are enforced
by the script.

```sh
cd packages/data
set -a
source .env
set +a
uv run scripts/phase_a_ducklake_spike.py run --reset
```

Run the `verify` command from a second configured machine to prove its read
path. To complete the concurrent cross-machine check, start the following on two
machines at the same time, using a distinct source label on each:

```sh
uv run scripts/phase_a_ducklake_spike.py append --source "$(hostname)-phase-a"
```

Each command must report 20 rows for its source, and a subsequent `verify` must
include both batches in the total. The automatic `run` command uses two local
worker processes; that proves catalog conflict handling but not the full network
path from a second host.
