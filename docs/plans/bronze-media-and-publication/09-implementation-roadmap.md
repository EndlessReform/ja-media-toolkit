# Implementation roadmap

Status: Phase 1 authorized. Later phases require explicit sign-off.

## Phase 1: external bronze registration

1. Define partitioned `bronze_capture` as an external Dagster `AssetSpec`.
2. Make the repair sensor register capture IDs and report materialization events
   rather than launch a bronze job.
3. Use manifest ETag as the external data version.
4. Attach manifest key, size, timestamp, schema, series hint, and track count as
   event metadata.
5. Test that the asset is non-executable and event identity is stable.

Gate: Dagster validates the definitions, tests pass, and no
`bronze_capture_job` exists.

## Phase 2: PostgreSQL ledger foundation

1. Add SQLAlchemy, Psycopg, and Alembic from `packages/data` using `uv add`.
2. Add settings for `JA_MEDIA_DATA_DATABASE_URL` without reading or committing
   secrets.
3. Create the tables and constraints in
   `03-partitions-identity-and-binding.md`.
4. Add focused repository methods and transaction tests.
5. Index externally observed capture headers into `bronze_captures`.

Gate: point lookups and binding conflicts are tested locally against PostgreSQL;
the `dagster` and `ja_media_data` databases remain isolated.

## Phase 3: episode hint and binding proof

1. Add the shared typed bronze manifest contract in `packages/core`.
2. Save fixtures for zero, one, and multiple subtitle tracks plus malformed and
   missing-object cases.
3. Implement one deliberately narrow, versioned filename-hint recipe.
4. Accept one binding transactionally and register its episode partition.
5. Record one ambiguous/overlapping result in `episode_resolution_issues`.
6. Change the recipe version and rebuild only affected capture partitions.

Gate: the UI and database explain both the accepted and quarantined decisions;
loss of Dagster metadata does not erase domain meaning.

## Phase 4: ledger snapshots and Garage artifact adapter

1. Export consistent ledger tables to versioned Parquet and commit
   `manifest.json` last.
2. Add `MediaArtifactRef` and the generic Garage artifact envelope.
3. Prove idempotent commit and recovery after an interrupted upload.

Gate: snapshots are queryable without PostgreSQL, normal incremental lookups do
not hit Garage, and derived bytes remain intelligible without Dagster run IDs.

## Phase 5: first real silver vertical slice

1. Extract `portable-aac-v1` from the current audio-library materialization.
2. Add versioned audio LID and a blocking Japanese-audio policy.
3. Add immutable/current `display-v1` bundles.
4. Run a targeted stale/rebuild scenario across one series.

Gate: the Japanese episode publishes a bundle, the dub remains blocked, and old
binding/artifact/bundle history remains reproducible.

## Later gates

- Consumer services index/read gold bundles, never Dagster tables.
- Capture-first ingest notifies Dagster only after the bronze manifest commits.
- Subtitle normalization, LID, alignment, and cleaning are added only for a
  concrete caller.
- Pinned datasets and packed exports remain immutable projections.

Before subtitle integration, split the existing 717-line
`kitsunekko_subtitles/app.py`; it is a hard-limit violation and must not grow.

## Local verification

```text
uv run pytest packages/data/tests
cd packages/data && uv run dg check defs
```

Add package/core/service/site tests only when their corresponding phase changes
those components. Remote database creation, deployment, restarts, and storage
verification remain user-owned.
