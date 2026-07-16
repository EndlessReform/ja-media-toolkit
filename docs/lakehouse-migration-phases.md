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

1. Add `packages/data/schema/` SQL defining `bronze_captures`, `episode_hints`,
   `episode_bindings`, and `episode_resolution_issues`, plus a nullable
   `run_source`; also define `run_log`.
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
- minimal issue and `run_log` rows proving those schemas are usable.

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

**Gate:** `ja-data resolve-sample --limit 100 --apply` reproduces the
baseline: 52 accepted, 48 quarantined, with the same reasons.

## Phase C2 — reshape writes and restore the PostgreSQL decision boundary (done)

Review after Phase C (2026-07-14) found that Phases B–C ported the Postgres
ledger's event-sourced, row-at-a-time write discipline into DuckLake and then
re-implemented, by hand, the constraint machinery the substrate deliberately
omits — the "cutting against the grain" both reports observed was
self-inflicted by the ported shape, not demanded by the problem. The amended
architecture (plan: "Binding product") splits machine derivations from human
decisions. A subsequent review on 2026-07-15 restored ordinary PostgreSQL as
the owner of the small, invariant-bearing human decision set. This phase pays
both corrections before any kernel or TUI code builds on the ledger shape.

1. **Done (2026-07-15):** replace `episode_hints` / `episode_bindings` /
   `episode_resolution_issues` with `episode_hints_auto`,
   `episode_bindings_auto`, `resolution_issues_auto` (derived, replaced per
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

**Gate:** the 100-capture corpus reproduces 52 accepted / 48 quarantined with
the same reasons through the batch writer; an identical second run is a
fingerprint no-op with **zero** table writes (not "expected cache churn"); an
appended override is immediately visible in the effective-binding query with
no rebuild step; a competing capture override fails atomically in PostgreSQL;
the prior automatic product remains readable via snapshot time travel.

**Validated 2026-07-15:** the isolated local PostgreSQL/MinIO run produced 96
hints, 52 automatic bindings, 48 issues, and zero consistency findings. An
identical second run reported `bronze_written=false`,
`resolution_written=false`, and `flushed_tables=0`; the first product remained
queryable by snapshot version in the repository test.

## Phase D0 — operator visibility slice

Build a thin read-only Textual surface over real C2 state before adding more
pipeline machinery:

1. Define a UI-independent status projection for captures, automatic and
   effective bindings, override provenance, resolution issues, consistency
   findings, fingerprints, and materialization timestamps.
2. Render an episode × stage matrix with refresh, filtering, and a detail pane.
3. Register LID, alignment, and publication as explicit `not_implemented`
   stages. A stub never emits domain output or masquerades as pending, stale,
   successful, or failed work.
4. Keep the first slice read-only. Widgets call the status projection rather
   than querying DuckLake or PostgreSQL directly.

**Gate:** the operator can inspect the 100-capture C2 result, distinguish
automatic bindings, overrides, explicit unbinds, and quarantines, and see that
future stages are intentionally unavailable. The same projection is testable
without Textual.

## Phase D — execution kernel and vertical slice

Prove the execution contract on portable audio + LID behind the established
status projection:

1. Implement the target registry and fingerprint staleness query first
   (stale = stored fingerprint ≠ fingerprint recomputed from current
   upstreams), tested with missing, stale, and override-shadowed inputs.
2. Per-stage TOML recipe files loaded through Pydantic; the recipe content
   hash feeds the fingerprint.
3. Scratch-then-replace stage runners, `run_log`, and re-run semantics.
4. Add `ja-data status [--series <id>]` and
   `ja-data run <target> [--series <id> | --set <name>]` with "everything
   stale" as the default scope, plus `ja-data targets` introspection.
5. Replace the LID `not_implemented` state with real missing/current/stale/
   failed status; do not change widget-side storage logic.
6. Demonstrate: default stale-set selection across many series; walking the
   closure to a fork (two targets sharing ancestry, computed once); mid-run
   kill leaving the previous table version intact; completion on another
   machine; a recipe edit staling exactly its downstream; truthful status
   rendering.

**Gate:** every demonstration passes and the TUI reports the same state as the
CLI status command.

## Phase E — safe operator actions

Add light mutation and execution actions to the proven read-only TUI: binding
override/unbind, issue navigation, and local-capability-aware stage dispatch.

**Gate:** on the laptop, the operator sees a series, runs the next light stage
for a stale episode, resolves an issue, and copies no hash or runs list commands.
Heavy stages show as runnable elsewhere when local capability is absent.

## Phase F — remove Dagster and the old substrate (repository done)

The legacy modules, tests, Alembic migration, dependencies, `tool.dg`
configuration, deployment, image, and superseded design documents are removed.
`ledger_types.py` became `resolution_types.py` with only the records used by the
C2 compiler and PostgreSQL override boundary.

`deploy/metaflow/` is retained because it belongs to the separate evaluation
workbench proposal in `eval-workbench-design.md`; it is not an execution option
for this data layer. The remaining Dagster database/principal cleanup is an
explicit user-owned infrastructure operation, not a repository compatibility
requirement.

**Gate:** no Dagster, SQLAlchemy, or Alembic imports remain; Postgres holds the
DuckLake catalog plus the deliberately small `binding_overrides` control-plane
table; the data-package suite and local object-storage smoke are green.
Repository portion validated 2026-07-15; external database cleanup remains
user-owned.

## Phase G (optional) — dbt

Use `dbt-duckdb` for tests, documented lineage, and a transformation registry
only if the SQL surface outgrows checked-in schema files. Adoption remains
incremental and optional.
