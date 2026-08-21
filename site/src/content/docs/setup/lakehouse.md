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

The deployment schema entrypoint attaches the PostgreSQL-backed DuckLake and applies both
sets of numbered SQL files: DuckLake relations from
`packages/data/migrations/ducklake/` and ordinary PostgreSQL decisions from
`packages/data/migrations/control_postgres/`. Each schema records SHA-256 checksums;
re-running is a no-op, and editing an applied file is rejected.

```sh
uv run --directory packages/data ja-data-schema-init
```

`ja-data` merges `.env` files from the Git repository root through its
invocation directory, with more specific files winning. Already-exported
process variables remain authoritative, and discovery never crosses the
repository boundary.

Stable catalog, object-store, bronze, control-schema, and service settings live
in the selected `config.<environment>.toml`. `pydantic-settings` applies nested
environment overrides such as `JA_MEDIA_DUCKLAKE__POSTGRES_URL` for secrets.
The adapter creates the PostgreSQL metadata schema when absent. Neither schema
path drops or resets existing state.

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

Start the disposable stack described in `deploy/data/local/README.md`, then
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

Start the disposable stack, then launch the bounded canary or canonicalization
job from Dagster. Product mutation belongs to Dagster; `ja-data` deliberately
does not expose `scan-bronze`, `resolve-sample`, or campaign-launch commands.
See [Lakehouse operator workbench](/setup/operator-workbench/).

Each product records a content fingerprint and lineage metadata in
`materializations`. Repeating an identical compilation avoids rewriting product
rows while Dagster records the execution. A changed input replaces the product
atomically; the previous version remains available through DuckLake snapshot
time travel.

Canonicalization first materializes `capture_audio_eligibility`, one decision
per committed Bronze capture. Legacy manifests remain eligible through their
single audio track. For schema v2, the current deliberately narrow policy pins
the first track whose `declared_language` is exactly `jpn`; captures without one
are retained as inspectable ineligible decisions instead of failing the corpus
run. Automatic binding selection chooses the newest eligible capture for an
episode, while an explicit override to an ineligible capture remains unresolved
and never silently falls back. The operator workbench exposes the decision,
reason, selected stream, and complete declared track headers in the existing
canonicalization stage inspector.

## Back up the catalog

Run from the deployment environment with its protected PostgreSQL DSN, then
make a custom-format dump. Replace the example schema names with the configured
environment's catalog and decision schemas.

```sh
pg_dump \
  --format=custom \
  --schema=ja_media_ducklake \
  --schema=ja_media_control \
  --file=/path/on-backed-up-storage/ja-media-ducklake-catalog.dump \
  "$JA_MEDIA_DUCKLAKE__POSTGRES_URL"
```

Include both the configured DuckLake catalog and control-plane schema names.
For a manual run, supply a standard `postgresql://` URL without printing it or
place the connection fields in the usual `PGHOST`, `PGPORT`, `PGDATABASE`,
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
uv run ja-data web
```

Open a known product in the workbench and confirm its rows load through the
restored catalog and referenced Garage objects.

Do not restore over a populated catalog. Restore into an empty target, verify
it, and only then change clients to use it.
