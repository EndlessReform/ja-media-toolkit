# ja-media data compiler

This package compiles immutable Garage evidence into replaceable DuckLake data
products and keeps human episode-binding overrides in ordinary PostgreSQL.
The supported implementation is the lakehouse modules and the `ja-data` CLI;
the retired Dagster, SQLAlchemy, and Alembic substrate has been removed.

Start with [ARCHITECTURE.md](ARCHITECTURE.md) before changing execution or the
operator workbench. It defines campaigns, global runs, local checkpoints,
materializations, currency, historical views, cache identities, and connection
ownership, and explains why those concepts are deliberately separate.

## Storage boundary

- Garage owns bronze media, manifests, and DuckLake Parquet files.
- DuckLake owns rebuildable `bronze_captures`, `episode_hints_auto`, binding
  proposals and automatic acceptances, canonical inputs, subtitle LID results,
  resolution issues, and materialization state.
- PostgreSQL owns the DuckLake catalog plus the small transactional
  `binding_overrides` table.
- Active PostgreSQL overrides mask, correct, or unbind automatically accepted
  proposals before canonicalization.

Automatic products are replaced as complete transactions. Their content
fingerprints avoid rewriting identical rows, while a revalidation checkpoint
still advances explicit lineage. History is DuckLake snapshot time travel
rather than supersession rows.

## Commands

`ja-data` loads repository-local environment files automatically. Starting at
the Git repository root, it merges every `.env` down to the invocation
directory. More specific files win; variables explicitly exported by the
launching process win over every file. It never searches above the repository
boundary.

For example, launching from the data package merges the root and package files:

```sh
cd packages/data
uv run ja-data web
```

The same rule applies to the compiler surface:

```sh
uv run ja-data apply-lakehouse-schema
uv run ja-data scan-bronze --limit 100
uv run ja-data resolve-sample --limit 100 --show issues
uv run ja-data resolve-sample --limit 100 --apply --show none
uv run ja-data resolution-report --limit 100
uv run ja-data targets
uv run ja-data run accepted-bindings
uv run ja-data run canonical-inputs
uv run ja-data run subtitle-lid
uv run ja-data run subtitle-lid --force-from canonical_inputs
uv run ja-data bind anilist 15451 3 --capture <capture-id>
uv run ja-data bind anilist 15451 3 --unbind
```

The DuckLake data prefix defaults to `audio/anime/lakehouse`. Set
`JA_MEDIA_DUCKLAKE_DATA_PATH` to replace the complete path or
`JA_MEDIA_DUCKLAKE_DATA_PREFIX` to override only that prefix.

`apply-lakehouse-schema` applies checksum-protected SQL from both `schema/`
and `postgres_schema/`. Phase D treats resolver rows as proposals, accepts all
currently auto-acceptable proposals under an explicit versioned policy,
chooses the latest accepted capture per episode, and runs LID on its subtitles.

## Operator workbench

`uv run ja-data web` serves the read-only workbench at
[http://127.0.0.1:8765/operator](http://127.0.0.1:8765/operator). It shows the
current canonical product first, a selectable pipeline spine, stage-owned
exceptions, numbered global runs, local stage executions, and historical
DuckLake snapshot views. Product and exception tables are server-paged, and
candidate evidence is loaded only when expanded.

The next gate adds evidence-bound operator decisions for competing canonical
candidates and bindings. It should reuse the existing PostgreSQL override
boundary and make downstream invalidation explicit; it is not authorization to
add a generic approval framework, scheduler, or remote executor.

## Tests

The data-package suite uses the disposable PostgreSQL catalog configured by
`deploy/lakehouse-dev/`:

```sh
uv run --directory packages/data pytest tests
```

Set `JA_MEDIA_PHASE_B_MINIO_SMOKE=1` to additionally exercise Parquet round
trips through the disposable MinIO bucket.
