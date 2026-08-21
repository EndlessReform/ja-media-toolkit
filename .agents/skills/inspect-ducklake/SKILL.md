---
name: inspect-ducklake
description: Export and analyze exact DEV DuckLake asset snapshots. Use for Dagster asset or materialization inspection, requests to pull the latest Silver/DuckLake table, snapshot-pinned JSONL or Parquet exports, and data-product error analysis. This is the required path for bulk DEV asset reads; never scrape the operator UI as a fallback.
---

# Inspect DuckLake

Use the bundled read-only exporter to dereference DuckLake-backed Dagster
assets. Dagster identifies the domain materialization and DuckLake snapshot;
DuckLake resolves the physical Parquet files.

## Export

Run from the repository root. The default is the latest durable domain
materialization for the asset:

```sh
UV_CACHE_DIR=/tmp/ja-media-uv-cache uv run --script \
  .agents/skills/inspect-ducklake/scripts/export_dev_asset.py \
  resolution_issues_auto
```

“Latest” means the newest committed row for the asset's owning target in the
DuckLake `materializations` catalog with `scope = 'corpus'`, ordered by `computed_at` and
`materialization_id`. It does not mean the newest Dagster event; a Dagster run
may reuse an existing domain materialization.

Pin an exact materialization when the user supplies one:

```sh
UV_CACHE_DIR=/tmp/ja-media-uv-cache uv run --script \
  .agents/skills/inspect-ducklake/scripts/export_dev_asset.py \
  resolution_issues_auto \
  --materialization-id \
  materialization-episode_resolution-b4cb85817b3642cc901d61c708f67532
```

Use `--snapshot ID` only when the user explicitly supplies a DuckLake snapshot.
Use `--format parquet` when Parquet is more convenient. Otherwise the script
writes JSONL under the gitignored `output/ducklake/` directory. Report the
printed materialization ID, snapshot, row count, output path, and checksum.

## Configuration

Use the standard developer DEV files:

```text
packages/data/config.dev.toml
packages/data/.env.dev
```

Create them from the adjacent checked-in examples. Stable endpoints, catalog
schema, and data path belong in TOML. The existing DEV PostgreSQL and Garage
credentials belong in `.env.dev`. `JA_MEDIA_DATA_CONFIG` may select another
standard data config explicitly.

Do not fall back to root `.env`, `config.local.toml`, or `.env.worker.dev`.
Do not print, inspect, or copy credential values into commands or logs.

## Failure rule

Never scrape or paginate the operator UI to extract asset rows. The operator UI
is a bounded presentation surface, not a data export API. If the exporter
cannot attach, resolve a materialization, or read a snapshot, stop and repair
the canonical DEV configuration or report the precise missing access.

Dagster GraphQL may be used read-only to resolve metadata from an exact asset
event URL when the user did not provide its materialization ID or snapshot.
Pass the resolved identifier to the exporter; do not parse rendered HTML.

## Analyze locally

Use DuckDB directly after export, for example:

```sh
duckdb -c "FROM read_ndjson_auto('output/ducklake/resolution_issues_auto-snapshot-36.jsonl') LIMIT 5"
```

Keep analysis local. Do not write to DEV DuckLake or Garage.
