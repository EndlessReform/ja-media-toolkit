# Lakehouse migration phases

This is the execution companion to
[lakehouse-migration-plan.md](lakehouse-migration-plan.md). The architecture,
ownership boundaries, and tradeoffs are decided there; this document owns the
ordered implementation work and its gates.

## Phase A — DuckLake-on-Garage spike (done)

Prove the substrate against this Garage before building anything on it:

1. `uv add duckdb` in `packages/data`. Attach
   `ducklake:postgres:<catalog dsn>` with `DATA_PATH` on the Garage endpoint,
   path-style addressing, and credentials via DuckDB secrets loaded from the
   environment (never printed).
2. Exercise `CREATE TABLE`, appends including data inlining and flush,
   `ALTER TABLE ADD COLUMN`, time-travel reads, snapshot expiry and file
   cleanup, and concurrent appends from two machines.
3. Verify recovery: `pg_dump` the catalog, destroy and restore it, confirm
   tables read correctly, and document the backup step in the runbook.

**Gate:** all of the above work against Garage; the recovery drill succeeds;
any Garage incompatibility is found now, at zero sunk cost.

## Phase B — schema and views (done; write shape superseded by Phase C2)

1. Add `packages/data/schema/` SQL defining the original
   `bronze_captures`, `episode_hints`, `episode_bindings`, and
   `episode_resolution_issues` spike, plus `run_source` and `run_log`. These
   historical names and the ledger-shaped write model were removed by C2/D2;
   they are recorded here only to explain the migration sequence.
2. Add `current_bindings` and `consistency_findings` views with the currency
   semantics in the architecture plan.
3. Test accept, reject-without-replacement, supersession, correction moves,
   and race-artifact detection.

### Fixture contract

Use a small synthetic identity graph, not media files and not the 100-capture
resolver corpus (that corpus enters in Phase C). Checked-in rows cover:

- an initial accepted binding;
- rejection without replacement, leaving the locator unbound;
- a superseding acceptance;
- correction of a locator from one capture to another;
- deliberate locator-side and capture-side collisions representing possible
  check-then-append races;
- the corresponding `consistency_findings`; and
- minimal issue and historical `run_log` rows proving those spike schemas were
  usable.

Use stable IDs and explicit ordering values so view tests do not depend on wall
clock timing or generated UUID order. Keep fixtures declarative and small enough
that each transition is legible in its test.

### Development validation

Use two layers:

1. The normal suite attaches DuckLake to the local PostgreSQL 17 test catalog
   and uses a temporary local filesystem `DATA_PATH`. This tests the production
   catalog type while keeping table files hermetic and fast.
2. One opt-in smoke test uses `deploy/lakehouse-dev/`: PostgreSQL 17 plus MinIO
   with a disposable bucket and path-style addressing. MinIO is an S3-compatible
   development surrogate, not a replacement for the Phase A Garage result. The
   smoke proves configuration and object-storage wiring only; ordinary tests do
   not require MinIO.

The stack owns no durable domain state. `docker compose down --volumes` is the
supported reset. Credentials, database, bucket, ports, and volumes are test-only.
Remote development or production services are never a fallback.

**Gate:** rejection without replacement returns *unbound*; all transition and
collision-detection tests pass; `apply_schema` is idempotent when applied twice
to a clean local PostgreSQL catalog; the opt-in MinIO smoke passes without
changing the local-filesystem results.

## Phase C — port the resolver (done; write shape superseded by Phase C2)

1. Replace `LedgerRepository` calls in `resolution_service.py` with DuckLake
   appends plus the pre-commit policy check.
2. Replace the Dagster bronze sensor with a scan script: list Garage manifest
   markers and upsert the rebuildable `bronze_captures` cache.
3. Port idempotency and conflict tests.

**Historical gate:** the then-supported applied 100-row command reproduced 52
auto-acceptable proposals and 48 quarantined rows. E2.1 later removed bounded
application; `resolve-sample` is now non-publishing and durable compilation is
Dagster-owned.

## Phase C2 — reshape writes and restore the PostgreSQL decision boundary (done)

Review after Phase C (2026-07-14) found that Phases B–C ported the Postgres
ledger's event-sourced, row-at-a-time write discipline into DuckLake and then
re-implemented, by hand, the constraint machinery the substrate deliberately
omits — the "cutting against the grain" both reports observed was
self-inflicted by the ported shape, not demanded by the problem. The amended
architecture (plan: "Binding product") splits machine derivations from human
decisions. A subsequent review on 2026-07-15 restored ordinary PostgreSQL as
the owner of the small, invariant-bearing human decision set. This phase pays
both corrections before any kernel or operator-surface code builds on the
ledger shape.

1. **Done (2026-07-15):** replace `episode_hints` / `episode_bindings` /
   `episode_resolution_issues` with `episode_hints_auto`,
   `episode_binding_proposals`, `resolution_issues_auto` (derived, replaced per
   recompute) plus `materializations` (target, scope, fingerprint,
   computed_at, run_id). Shrink `consistency_findings` to cross-substrate
   data-quality checks.
2. **Done (2026-07-15):** replace `DuckLakeRepository`'s guarded per-row appends with a batch
   writer: `replace_resolution_tables(results, fingerprint)` in one
   transaction. Uniqueness enforcement for automatic output moves into the
   compiler's in-memory pass; the repository independently rejects duplicate
   batch keys before starting a transaction. Delete the DuckLake head-check
   choreography and its `BindingConflictError` paths.
3. **Done (2026-07-15):** add ordinary PostgreSQL `binding_overrides` outside
   the DuckLake catalog schema plus a small psycopg repository.
   `append_override(...)` retires the prior locator head and inserts its
   replacement in one transaction; partial unique indexes reject locator and
   capture collisions. The effective-binding query takes the active override
   (including a NULL-capture unbind), otherwise the DuckLake automatic row.
   Checked-in SQL is applied beside, not inside, the DuckLake catalog without
   adding new SQLAlchemy or Alembic machinery.
4. **Done (2026-07-15):** delete `ordered_id` / `ordered_id_after` and their
   tests; `_stable_id` returns to the resolver module for deterministic
   automatic keys. No legacy DuckLake event tables or compatibility views
   remain in the supported clean-catalog schema.
5. **Done (2026-07-15):** `bronze_hydration` becomes rescan-and-replace; the capture-moved identity
   guard becomes a read-side finding.
6. **Done (2026-07-15):** `resolution_service` collects the batch, then writes once; delete
   `_prepare_safe_supersession` and `resolve_open_issues` (an override
   shadows its quarantine issue in status instead of mutating it).
7. **Done (2026-07-15):** tests re-express accept / override / unbind / correction transitions
   against the composed effective-binding query; delete the DuckLake
   race-artifact fixtures; replace
   per-row idempotency tests with "identical inputs produce an identical
   table, and a fingerprint match skips the write entirely". Include
   PostgreSQL constraint tests and missing/stale-capture findings.
8. **Done (2026-07-15):** rename the entry point `ja-media-data` → `ja-data`.
9. **Done (2026-07-15):** fix `catalog.py`'s packaged-schema path and verify
   both DuckLake and PostgreSQL SQL directories are present in the built wheel.

**Gate:** the 100-capture corpus reproduces 52 proposals / 48 quarantined with
the same reasons through the batch writer; an identical second run is a
fingerprint no-op with **zero** table writes (not "expected cache churn"); an
appended override is immediately visible in the effective-binding query with
no rebuild step; a competing capture override fails atomically in PostgreSQL;
the prior automatic product remains readable via snapshot time travel.

**Validated 2026-07-15:** the isolated local PostgreSQL/MinIO run produced 96
hints, 52 automatic proposals, 48 issues, and zero consistency findings. An
identical second run reported `bronze_written=false`,
`resolution_written=false`, and `flushed_tables=0`; the first product remained
queryable by snapshot version in the repository test.

## Phase D — binding-to-subtitle-LID vertical slice (implemented)

The 2026-07-15 implementation deliberately moved the first interactive surface
until after one real downstream path existed. The slice is:

```text
bronze capture
  -> episode binding proposal
  -> automatic acceptance gate
  -> canonical episode input
  -> subtitle LID result
```

1. Rename resolver output to `episode_binding_proposals`. Proposals are not a
   downstream contract.
2. Materialize `accepted_bindings_auto` through a separately versioned policy.
   Phase D policy `accept-resolver-proposals-v1` accepts every proposal the
   conservative resolver emits. This is intentionally permissive so
   canonicalization and LID see representative data; changing the policy does
   not change either downstream stage. Active PostgreSQL overrides still mask,
   correct, or unbind a locator before canonicalization.
3. Materialize `canonical_episode_inputs` and `canonical_subtitle_inputs`.
   When several accepted captures claim one locator, choose the greatest
   bronze commit marker's `manifest_modified_at`; break exact timestamp ties by
   manifest key and capture ID. This is the complete versioned Phase D policy,
   `latest-manifest-modified-v1`: “latest file wins at compile time.”
4. Run existing script-first/FastText-fallback subtitle language analysis over
   canonical subtitle objects only. Audio LID is deliberately absent.
5. Store exact input fingerprints, recipe/policy versions, materialization
   timestamps, and run rows. Identical target fingerprints do not rewrite
   product tables (the dispatch is still recorded as a global run and local
   stage checkpoint). A read,
   parse, or LID failure records a failed run and leaves the previous complete
   result table intact.
6. Expose the closure as `ja-data run subtitle-lid`; also expose the two
   intermediate targets and `ja-data targets` so individual boundaries can be
   exercised while debugging. These temporary custom-executor commands were
   removed in E2.1 after Dagster became the execution owner.

**Repository gate:** tests prove two competing acceptable proposals survive
the resolver, the newer capture wins canonicalization, only its subtitle is
classified, identical reruns do not rewrite products, and a failed rerun
preserves the prior result. Applying this schema and compiling the configured
shared corpus remain explicit operator actions; repository tests do not write
shared data services.

## Phase D1/D2 — read-only operator workbench and truthful runs (implemented)

The implemented slice is a surface-neutral operator application with typed JSON
and loopback FastAPI/Jinja/HTMX adapters. It deliberately registers only real
Phase D stages instead of manufacturing future mock targets. The campaign view:

1. leads with the paged canonical product and lazily loads candidate evidence;
2. exposes a selectable, horizontally scrollable pipeline spine with paged
   stage-owned exception/result views;
3. distinguishes the currently committed product, the global run that built
   it, the most recent stage execution, and dependency-derived currency;
4. records monotonically numbered global runs made of independently atomic
   local stage checkpoints;
5. supports exact historical workspace views through DuckLake snapshots; and
6. owns attached DuckLake/PostgreSQL clients and bounded projection caches in
   the FastAPI lifespan.

The UI remains read-only. Routes and templates consume application DTOs and do
not own storage queries, planning, staleness, or mutation rules. See
[`packages/data/ARCHITECTURE.md`](../packages/data/ARCHITECTURE.md) for the
implemented contracts; the broader workbench design remains directional rather
than a claim that every proposed future target exists.

**Gate status:** complete. The configured bronze corpus drives the campaign;
operators can explain canonical selection, inspect failure evidence, distinguish
mixed-generation stage state, expand bounded products, and inspect global/local
run lineage without copying opaque IDs.

## Phase E — product boundaries and orchestration decision (E2.1 complete)

Phase E selected Dagster as the execution authority after separating product
logic from orchestration. Durable decisions now live in
[`packages/data/ARCHITECTURE.md`](../packages/data/ARCHITECTURE.md); only
unfinished gates remain in
[`plans/lakehouse-phase-e2-control-plane-and-workers.md`](plans/lakehouse-phase-e2-control-plane-and-workers.md).

1. **Done (2026-07-18):** extract coherent product packages and
   orchestration-free compiler/commit boundaries without redesigning the
   current executor; validate the complete chain on 100 real bronze captures.
2. **Done (2026-07-18):** run a corrected unpartitioned Dagster collection proof
   over the real 100-capture canonicalization closure, including mixed-generation
   failure, override invalidation, quarantine, downstream rerun, and a
   non-publishing bounded canary. Accepted measurements were promoted to the
   package architecture and the disposable spike report was removed.
3. **Done (2026-07-19):** prove delayed native-worker claiming across a
   webserver restart on three full canonical episodes and receive explicit
   user acceptance of Dagster.
4. **Done (2026-07-19):** freeze campaign/worker/gateway/storage contracts,
   move the workbench spine and run views to public Dagster APIs, and delete the
   custom planner, registry, executor, and execution CLI.
5. **Next:** promote the VAD proof into the supported native-worker product
   boundary, then add input-head-bound canonicalization decisions.

**Gate:** no speculative replacement executor is built. Product extraction is
behavior-preserving; the Dagster proof either demonstrates collection lineage
without duplicated eligibility/materialization truth or is removed; the chosen
execution owner is explicit before the workbench gains mutations.

## Historical Phase F — remove the original Dagster spike (repository done)

The legacy modules, tests, Alembic migration, dependencies, `tool.dg`
configuration, deployment, image, and superseded design documents are removed.
`ledger_types.py` became `resolution_types.py` with only the records used by the
C2 compiler and PostgreSQL override boundary.

This records the removal of the rejected capture-partitioned implementation. It
does not prejudge Phase E's separate collection-asset proof. If that proof is
accepted, its Dagster integration starts cleanly and does not restore the old
dynamic partition registry, sensor, SQLAlchemy ledger, or Alembic substrate.

`deploy/metaflow/` is retained because it belongs to the separate evaluation
workbench proposal in `eval-workbench-design.md`; it is not an execution option
for this data layer. The remaining Dagster database/principal cleanup is an
explicit user-owned infrastructure operation, not a repository compatibility
requirement.

**Gate at completion (2026-07-15):** no imports from the original Dagster,
SQLAlchemy, or Alembic spike remained; PostgreSQL held the DuckLake catalog plus
the deliberately small `binding_overrides` control-plane table. Phase E1's new
collection-only Dagster adapter is a separate 2026-07-18 proof and does not
restore that capture-partitioned substrate. External database cleanup remains
user-owned.

## Phase G (optional) — dbt

Use `dbt-duckdb` for tests, documented lineage, and a transformation registry
only if the SQL surface outgrows checked-in schema files. Adoption remains
incremental and optional.
