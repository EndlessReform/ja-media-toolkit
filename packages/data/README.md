# ja-media data compiler

This package compiles immutable Garage evidence into replaceable DuckLake data
products and keeps human episode-binding overrides in ordinary PostgreSQL.
The supported implementation is the lakehouse modules and the `ja-data` CLI;
the retired Dagster, SQLAlchemy, and Alembic substrate has been removed.

## Storage boundary

- Garage owns bronze media, manifests, and DuckLake Parquet files.
- DuckLake owns rebuildable `bronze_captures`, `episode_hints_auto`,
  `episode_bindings_auto`, `resolution_issues_auto`, and materialization state.
- PostgreSQL owns the DuckLake catalog plus the small transactional
  `binding_overrides` table.
- Effective binding reads take an active PostgreSQL override first, including
  an explicit unbind, and otherwise use the automatic DuckLake row.

Automatic products are replaced as complete transactions. Their content
fingerprints make an identical repeat a zero-write no-op; history is DuckLake
snapshot time travel rather than supersession rows.

## Commands

Load the package environment without printing it:

```sh
cd packages/data
set -a
source .env
set +a
```

Then use the compiler surface:

```sh
uv run ja-data apply-lakehouse-schema
uv run ja-data scan-bronze --limit 100
uv run ja-data resolve-sample --limit 100 --show issues
uv run ja-data resolve-sample --limit 100 --apply --show none
uv run ja-data resolution-report --limit 100
uv run ja-data bind anilist 15451 3 --capture <capture-id>
uv run ja-data bind anilist 15451 3 --unbind
```

`apply-lakehouse-schema` applies checksum-protected SQL from both `schema/`
and `postgres_schema/`. Schema files describe the final C2 contracts only;
reset disposable pre-C2 catalogs instead of attempting an in-place upgrade.

## Tests

The data-package suite uses the disposable PostgreSQL catalog configured by
`deploy/lakehouse-dev/`:

```sh
uv run --directory packages/data pytest tests
```

Set `JA_MEDIA_PHASE_B_MINIO_SMOKE=1` to additionally exercise Parquet round
trips through the disposable MinIO bucket.
