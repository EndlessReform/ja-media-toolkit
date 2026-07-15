# Implementation roadmap

Status: Phases 1 and 2 complete. The Phase 3 resolver and 100-capture slice are
complete. Its per-record downstream asset model is rejected and must be
reshaped before Phase 4 or 5 proceeds.

## Phase 1: external bronze registration — complete

1. Define partitioned `bronze_capture` as an external Dagster `AssetSpec`.
2. Make the repair sensor register capture IDs and report materialization events
   rather than launch a bronze job.
3. Use manifest ETag as the external data version.
4. Attach manifest key, size, timestamp, schema, series hint, and track count as
   event metadata.
5. Test that the asset is non-executable and event identity is stable.

Gate: Dagster validates the definitions, tests pass, and no
`bronze_capture_job` exists.

Result: the capture is an external `AssetSpec`; the sensor reports runless,
ETag-versioned materializations and never launches source-capture compute.

## Phase 2: PostgreSQL ledger foundation — complete

1. Add SQLAlchemy, Psycopg, and Alembic from `packages/data` using `uv add`.
2. Add settings for `JA_MEDIA_DATA_DATABASE_URL` without reading or committing
   secrets.
3. Create the tables and constraints in
   `03-partitions-identity-and-binding.md`.
4. Add focused repository methods and transaction tests.
5. Index externally observed capture headers into `bronze_captures`.

Gate: point lookups and binding conflicts are tested locally against PostgreSQL;
the `dagster` and `ja_media_data` databases remain isolated.

Result: migration `20260713_0001` is applied to the UTF-8 development database,
Alembic reports no schema drift, and the repository's lookup and conflict paths
pass against disposable local PostgreSQL. The capture sensor indexes manifest
headers without moving media bytes through PostgreSQL.

## Phase 3: episode hint and binding proof — spike complete, sign-off pending

1. Add the shared typed bronze manifest contract in `packages/core`.
2. Save fixtures for zero, one, and multiple subtitle tracks plus malformed and
   missing-object cases.
3. Implement one deliberately narrow, versioned filename-hint recipe.
4. Accept one binding transactionally.
5. Record one ambiguous/overlapping result in `episode_resolution_issues`.
6. Change the recipe version and rebuild only affected capture partitions.

Gate: the UI and database explain both the accepted and quarantined decisions;
loss of Dagster metadata does not erase domain meaning.

Result: 52 of the first 100 captures were accepted and 48 were quarantined with
durable reasons. See `10-phase3-spike-report.md`. Capture-keyed resolution was
useful, but the conditional `validated_episode_mapping` asset and proposed
locator partition registry are not an acceptable downstream abstraction.

## Phase 3b: reshape orchestration before continuing

### Remove from the executable graph

1. Remove `validated_episode_mapping` from
   `packages/data/src/ja_media_data/episode_assets.py` and from
   `definitions.py`.
2. Remove the `stable_episode_mapping` asset check. Its acceptance predicates
   remain typed resolver/domain logic and PostgreSQL evidence, not an omitted
   materialization.
3. Remove tests and README claims that quarantined captures are represented by
   an optional downstream asset.
4. Do not add a dynamic partition definition populated from accepted locator
   rows.

### Preserve

1. External `bronze_capture` observation and manifest ETag data versions.
2. The typed bronze manifest parser, PTN/explicit-token evidence, AniList
   metadata boundary, resolver policy, CLI diagnostics, and corpus fixtures.
3. SQLAlchemy/Alembic models, repository transactions, immutable hints and
   decisions, current-binding constraints, and review issues.
4. The measured 100-capture report and all idempotency/conflict tests.

### Build the replacement proof

1. Define a collection-level `episode_identity_ledger` asset backed by the
   PostgreSQL relations.
2. Inside its update graph, select a bounded set of unresolved or stale
   captures and map the existing resolver over typed work items.
3. Accept semantic run configuration such as AniList series, episode range,
   issue kind, and `limit`; resolve internal IDs in repository code rather than
   requiring users to copy them.
4. Commit each success idempotently. Make a retry adopt committed successes and
   select only remaining/stale work.
5. Materialize collection metadata: selected, already-current, accepted,
   quarantined, operationally failed, input watermark, and recipe version.
6. Expose `stable_episode_inventory` and the open issue queue as PostgreSQL
   relations/views. They are durable products, not synchronized per-row
   Dagster partition registries.

Gate: one bounded run is inspectable in Dagster, an injected operational
failure retries cleanly without repeating committed work, and domain ambiguity
does not fail the collection update.

## Phase 4: ledger snapshots and Garage artifact adapter

1. Export consistent ledger tables to versioned Parquet and commit
   `manifest.json` last.
2. Add `MediaArtifactRef` and the generic Garage artifact envelope.
3. Prove idempotent commit and recovery after an interrupted upload.

Gate: snapshots are queryable without PostgreSQL, normal incremental lookups do
not hit Garage, and derived bytes remain intelligible without Dagster run IDs.

Do not begin until Phase 3b establishes whether Dagster adds useful retry and
inspection behavior beyond the PostgreSQL ledger.

## Phase 5: first real silver vertical slice

1. Add collection-level `portable_audio` backed by immutable Garage artifacts
   and a PostgreSQL result catalog.
2. Map conversion tasks over only eligible missing/stale stable episodes.
3. Add collection-level versioned audio LID results and a Japanese-audio
   selection policy.
4. Add immutable/current `display-v1` bundles.
5. Run a targeted stale/rebuild scenario across one series, including a
   partially failed run and retry.

Gate: the Japanese episode publishes a bundle, the dub remains excluded without
a useless downstream task cascade, old binding/artifact/bundle history remains
reproducible, and the Dagster UI is materially more useful than the equivalent
PostgreSQL-backed CLI. If not, compare this slice with Prefect before approving
the orchestrator.

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
