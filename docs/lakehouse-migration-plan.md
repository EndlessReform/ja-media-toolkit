# Lakehouse migration: DuckLake on Garage, retire Dagster

Status: proposed. Awaiting review before implementation.

This plan replaces the Dagster + Alembic + PostgreSQL-domain-tables model from
`docs/plans/bronze-media-and-publication/` with a single-substrate lakehouse:
DuckLake tables on Garage with the existing PostgreSQL instance as the DuckLake
catalog, a small explicit execution kernel, and CLI + Textual surfaces for the
operator. dbt is an optional later phase, not a prerequisite.

## Decision summary

- **Retire Dagster entirely** — webserver, daemon, dedicated database, asset
  definitions, sensor, and the `deploy/dagster/` deployment.
- **Adopt DuckLake as the domain substrate.** Domain tables become Parquet on
  Garage managed by the DuckLake catalog in Postgres. One copy of the data, in
  an open format, transactional across machines.
- **Do not adopt Delta Lake.** DuckLake and Delta are competing table formats,
  not composable layers; DuckLake alone covers every requirement here and its
  commit safety comes from Postgres transactions rather than S3 conditional
  writes, which Garage does not currently guarantee.
- **Keep the resolver and all domain logic.** The episode-resolution planner,
  evidence types, diagnostics, and the 100-capture behavioral baseline carry
  over unchanged except for persistence calls.
- **Own a small, explicit execution kernel** (staleness computation, run log,
  idempotent stage commits) instead of hiding it inside UI actions. No leases,
  no heartbeats, no scheduler — manual dispatch is the design, not a gap.
- **Binding invariants move from enforced to detected-and-repaired**, with a
  documented one-table Postgres fallback if detection ever proves
  insufficient in practice.

## The problem being solved

The operator is a single person managing native Japanese media on a personal
data lake, across several machines:

- **Manual dispatch.** The operator decides when to work, on which machine,
  and on which series. There is no SLA, no downstream consumer on a schedule,
  and no benefit to automatic triggering. A sensor that auto-launches Demucs
  on a sleeping CUDA box creates failed runs and noise, not value.
- **Machines are frequently down.** Heavy compute (Demucs, ASR) runs on a
  CUDA workstation or Apple Silicon box that sleeps when unused. Light work
  (resolution, LID, bundling) runs on the laptop or the always-on server.
- **Cross-machine sessions.** Work may start on one machine, pause, and
  resume on another. Shared state must live somewhere every machine can
  reach: today that is Postgres and Garage over tailnet.
- **Rapid ML iteration.** LID, alignment, and cleaning recipes change
  frequently. Each iteration is a new recipe version producing new rows; the
  operator wants v2 and v3 side by side, and to GC the loser later.
- **The measured pain is coordination and visibility.** In a 5+ stage
  pipeline the operator currently copies hashes between commands and runs
  several list commands to learn "what's the latest." The desired surface is
  a status matrix — episodes × pipeline stages — with the ability to walk a
  subsection of the graph to a pause point without ceremony.

## Evidence base

Honest labeling, per the architectural decision protocol:

**Measured (Phase 1–3 spike, `10-phase3-spike-report.md`):**

- The resolver works: 100 captures → 96 hints, 52 accepted bindings, 48
  quarantined with informative reasons. This is the behavioral baseline.
- Per-record Dagster assets failed concretely: conditional outputs made
  quarantine look like missing data; blocking checks turned legitimate
  quarantines into failed runs; partition mappings could not express the
  data-dependent capture→locator fan-in without duplicating Postgres
  eligibility state into a second registry. Both repairs were rejected.
- Postgres uniqueness and row locking correctly enforced the tested identity
  and idempotency rules.

**Assumed (not yet measured):**

- DuckLake behavior against Garage specifically (Phase A exists to measure
  this before anything else is built).
- The real size of the execution kernel and TUI (budgeted conservatively
  below, with a vertical-slice gate before the TUI is built).
- Corpus-scale query performance in either substrate. Note that performance
  is *not* the argument for this migration; at this corpus size Postgres
  would also be fast. The argument is single-copy storage and the removal of
  an entire export pipeline (below).

## Why Dagster is retired

The spike's orchestration findings, plus three structural mismatches:

1. **Dagster's core value is automation, which is actively unwanted here.**
   Sensors, schedules, auto-materialization, and backfill policies are the
   product. The operator's constraint is the opposite: machines are asleep by
   design and dispatch is manual. What remains after subtracting automation
   is run history and a web UI — a remote control with a screen, paid for
   with an always-on webserver, daemon, and dedicated Postgres database.
2. **The UI is the wrong shape.** The operator thinks in episodes × stages
   (`e001: capture✓ binding✓ lid✓ align stale bundle —`), not in asset-node
   topology. No amount of Dagster configuration produces that projection.
3. **The asset vocabulary wraps a data model that is simpler without it.**
   The collection-level graph is a SQL DAG over tables. Expressing it as
   asset decorators, I/O managers, and partition definitions adds a
   translation layer between the operator and the data without adding
   capability — the spike's own retreat to "collection-level assets with
   internal mapped tasks" concedes that the per-record model was the only
   distinctive thing Dagster was doing.

The existing plan's Phase 5 gate asked whether the Dagster UI beats "the
equivalent PostgreSQL-backed CLI." This plan answers that question in the
negative by construction and replaces the comparison slice with a concrete
vertical-slice gate (Phase D).

**What retiring Dagster actually costs, and how each cost is repaid:**

| Lost | Replacement |
| --- | --- |
| Run history and logs for long jobs | `run_log` table + log files in Garage scratch (Phase D) |
| Stale detection via code versions | Explicit fingerprint/recipe staleness query (Phase D, prototyped first) |
| Retries | Idempotent stage commits + the operator pressing `r` again |
| Asset lineage graph | ~12 tables whose lineage fits in one diagram in this document; optional dbt docs later |
| Web UI | Textual TUI + `ja-media status` (Phase E) |

## Why the substrate changes: one copy of the data

The existing plan stores silver collections as Postgres rows managed by
Alembic, **and** exports versioned Parquet snapshots to Garage for recovery
and analytics (`03-partitions-identity-and-binding.md`). That is two copies of
every table plus a synchronization pipeline between them — export jobs,
snapshot manifests, and a permanent "which copy is current" question.

DuckLake collapses this: **the Parquet on Garage is the table.** There is no
export pipeline because there is nothing to export; recovery and analytics
read the same files the pipeline writes. Secondary benefits follow from the
same collapse:

- **Schema evolution without a migration framework.** `ALTER TABLE ADD
  COLUMN` is a transactional catalog operation; old Parquet files remain
  readable (missing column reads as NULL). DDL still lives in git (see
  "Schema management") — what is removed is the ORM, the migration
  framework, and the env.py apparatus, not review.
- **Time travel and snapshot expiry are built in**, replacing hand-rolled
  snapshot versioning.
- **Small appends are cheap.** DuckLake's data inlining stores small inserts
  in the catalog and flushes them to Parquet later, which fits this
  workload's one-binding-row-at-a-time writes without tiny-file sprawl.
- **The boilerplate goes away.** `models.py` (196 lines), `repository.py`
  (290 lines), and the Alembic directory exist to give Postgres an ORM
  surface over data that is almost entirely append-only. The DuckLake write
  path is an `INSERT`; the read path is SQL.

Postgres is **not removed** — it is repurposed as the DuckLake catalog. It is
already always-on, flash-backed, and tailnet-reachable from every operator
machine, which is exactly the profile a catalog needs.

### What DuckLake is (for the record)

DuckLake is a lakehouse format in which a small SQL database (the *catalog*)
holds all table metadata — schemas, snapshots, statistics, and the list of
Parquet data files — while the data itself is plain Parquet on object
storage. DuckDB attaches the catalog (`ATTACH 'ducklake:postgres:...'` with a
`DATA_PATH` pointing at Garage) and then ordinary SQL reads and writes the
tables. Commits are transactions against the catalog database, which is what
makes multi-machine access safe on Garage: correctness never depends on S3
conditional-write semantics, only on Postgres transactions.

Two consequences must be stated plainly:

1. **The catalog is load-bearing for recovery.** It *is* the transaction
   log. It is not rebuildable from the Parquet files; losing it loses
   snapshot history and table membership. `pg_dump` of the catalog database
   (kilobytes to low megabytes) is therefore part of the backup regimen, not
   an optional nicety.
2. **DuckLake has no primary keys, unique constraints, foreign keys, or
   CHECK constraints**, and its maintainers consider key constraints
   unlikely ever to be supported. The invariant posture below addresses
   this head-on rather than pretending it away.

## Target architecture

```text
Garage (S3, tailnet)                      Postgres (flash, tailnet)
────────────────────                      ─────────────────────────
bronze manifests + media bytes            DuckLake catalog database
silver artifacts (portable audio, ...)      (schemas, snapshots, file
gold bundles + published projections         lists — pg_dump'd)
DuckLake Parquet data files
worker scratch (lifecycle-reaped)

DuckLake tables (Parquet on Garage, catalog in Postgres)
────────────────────────────────────────────────────────
bronze_captures            rebuildable index over bronze manifests
episode_hints              append-only resolver claims
episode_bindings           append-only decisions = the audit log
episode_resolution_issues  append + rare status update
audio_language_results     append-only, recipe-versioned
subtitle_alignments        append-only, recipe-versioned
episode_bundles            append-only, policy-versioned
run_log                    append-only execution history
current_bindings           VIEW (derived, never stored)
consistency_findings       VIEW (invariant checks, surfaced as issues)

Execution kernel (packages/data)          Operator surfaces (packages/frontend)
────────────────────────────────          ─────────────────────────────────────
staleness/eligibility queries             ja-media status --series <id>
stage runners (resolve, LID, align,       ja-media run --through <stage> ...
  transcode, bundle) with idempotent      ja-media lakehouse   (Textual TUI:
  commits and run_log entries               status matrix, detail panel,
                                            issue browser, light actions)
```

### Ownership boundaries

- **Garage owns bytes**: bronze media and manifests, silver media artifacts,
  gold bundles, DuckLake Parquet, large logs, scratch.
- **Postgres owns exactly one thing**: the DuckLake catalog. The operator
  never writes DDL against Postgres directly; DuckLake owns its catalog
  schema. (The documented fallback in "Invariant posture" is the sole
  potential exception, and it is not built now.)
- **DuckLake tables own facts and decisions**: what exists, what was
  claimed, what was decided, what ran.
- **The execution kernel owns semantics**: what is stale, what is eligible,
  what running a stage means, how results commit.
- **CLI and TUI own presentation and dispatch only.** They are two views
  over the same kernel functions and contain no pipeline logic.

### Binding currency semantics

This section replaces both the mutable `current_episode_bindings` table and
the naive "latest accepted row" view, which mishandles revocation.

`episode_bindings` is append-only. Every row is a decision with `binding_id`
(monotonic, generated outside the database), `decision` (`accepted` |
`rejected`), `supersedes_binding_id`, method, evidence JSON, and provenance
columns. The table **is** the audit log: rollback is a superseding row,
human override is a row with `method='human'`.

`current_bindings` is a view defined as: *for each locator, take the single
latest decision (ordered by `binding_id`, which is monotonic and unique — no
timestamp ties); the locator is currently bound if and only if that latest
decision is an acceptance.* This handles the case the naive view gets wrong:

1. Binding A accepted → locator bound to A.
2. A explicitly rejected (superseding row, `decision='rejected'`) → locator
   **unbound**, because the latest decision is a rejection. Filtering to
   accepted rows before taking the latest would incorrectly resurrect A.
3. Binding B accepted later → locator bound to B.

The same construction keyed by `audio_capture_id` yields the capture-side
view. Phase B ships tests for exactly these transitions, plus supersession
chains and correction moves, against the 100-capture fixture.

### Invariant posture: detect and repair

The Postgres ledger enforced two uniqueness invariants at write time:
one current binding per locator, and one current locator per capture
(`UNIQUE(audio_capture_id)`). DuckLake cannot enforce these. The posture
changes from **prevented** to **detected and repaired**, and this plan states
that trade explicitly rather than minimizing it:

- **Writes perform a pre-commit policy check** (query `current_bindings`,
  verify the locator and capture heads match expectations, then append).
  Under DuckLake this check-then-append is not atomic across machines; a
  race between two writers is possible in principle.
- **The realistic writer population is one human**, occasionally overlapping
  a forgotten terminal. The damage mode of a race is two decision rows where
  the view deterministically picks one (highest `binding_id`) and the other
  is implicitly superseded — an untidy history, not corruption.
- **A `consistency_findings` view runs the invariant checks on read**: any
  capture currently bound under two locators, any locator with anomalous
  decision chains, any binding whose `supersedes_binding_id` does not match
  the head it replaced. Findings surface in the TUI Issues column and in
  `ja-media status`. Repair is an ordinary superseding append.
- **Pressure valve (documented, not built):** because the DuckLake catalog
  is Postgres, a single plain table beside the catalog with
  `INSERT ... ON CONFLICT` semantics can restore write-time enforcement of
  the binding heads for ~40 lines of code, without reintroducing SQLAlchemy
  or Alembic. If detection ever fails the operator in practice, this is the
  first and only escalation.

### Schema management: DDL in git

No Alembic, but no ad-hoc laptop DDL either. `packages/data/schema/` holds
numbered, checked-in SQL files (`001_identity.sql`, `002_results.sql`, ...),
each applied transactionally through DuckLake, plus a tiny idempotent
`apply_schema` entrypoint that records applied files in a `schema_history`
table. Adding a column for LID v3 is a reviewed one-line SQL file, not a
migration module — review is preserved, the framework is removed. Clean
environments are reproducible by applying the directory in order.

### Execution kernel

The part of orchestration this pipeline genuinely needs, made explicit and
owned as code rather than hidden inside UI actions:

- **Staleness and eligibility queries** (`status_query` module). A result is
  current only when its input fingerprint and recipe/model version match the
  current upstream head; superseded bindings cascade staleness downstream.
  This is the six-state vocabulary from
  `05-invalidation-rebuilds-and-retention.md` (missing / failed / stale /
  superseded / quarantined / deleted) compiled into SQL over the DuckLake
  tables. **This is the hardest module and is prototyped first in Phase D**,
  because if it is wrong the status matrix displays lies.
- **`run_log`**, append-only: stage, target selector, input fingerprints,
  recipe versions, machine, started/finished timestamps, exit status, and a
  Garage path for captured logs. This answers "did that 90-minute ASR run I
  started before dinner finish?" without an orchestrator database, and gives
  the TUI a last-failure column. A run that never wrote a terminal row is
  visibly abandoned; because commits are idempotent, the recovery procedure
  is simply to run the stage again.
- **Idempotent stage commits.** Every stage computes to scratch, then
  commits results and `run_log` terminal row in one DuckLake transaction
  keyed by (target, input fingerprint, recipe version). Re-running a
  committed stage is a no-op; re-running an interrupted one adopts nothing
  and redoes the work.

Deliberately absent, because manual single-operator dispatch is the design:
schedulers, sensors, work queues, leases, heartbeats, ownership records, and
cancellation machinery. Those exist to arbitrate between competing
autonomous workers; there are none here. If parallel mapped execution within
a stage ever matters, it lives inside that stage's runner as ordinary
concurrent code committing one transaction, not as durable coordination
state.

### Dispatch model: status is global, execution is local

The TUI and CLI never remotely execute anything. The contract:

- **Heavy stages run where the hardware is**: the operator SSHes to (or sits
  at) the CUDA box or Mac and runs `ja-media run --through <stage>` there.
  Platform checks in the stage runners refuse work the local machine cannot
  do.
- **Light stages and decisions run anywhere**: binding acceptance, issue
  resolution, and bundling are fine from the laptop TUI.
- **The lakehouse is the rendezvous.** Because every machine reads and
  writes the same catalog and Parquet over tailnet, a laptop TUI left open
  shows results appearing as the workstation commits them (on refresh).
  "Resume on another machine" requires no session state — the durable
  tables *are* the session.

### Volatility, GC, and retention

Each recipe iteration writes new rows tagged `recipe_version`; old rows
remain for comparison and time travel. GC is explicit, separate, and
conservative, per `05-invalidation-rebuilds-and-retention.md`:

- Row-level GC of a losing recipe version is a DuckLake `DELETE` (a
  rewrite-based operation — rows from several recipes may share Parquet
  files, so deletion is logical first) followed by snapshot expiry and file
  cleanup, which physically removes files no longer referenced by any
  retained snapshot. Time-travel depth and reclaimed space trade against
  each other; the retention window is an explicit setting, not an accident.
- Garage byte artifacts (portable audio, bundles) keep the existing
  mark-and-sweep policy with dry-run defaults and tombstone reports.
  "Unreferenced" is computed from the domain tables (bundles, datasets,
  pinned selections), which is a domain reference graph, not a snapshot
  question.
- Scratch (`tmp/`, resamples, partial tensors) never enters DuckLake and is
  reaped by the existing lifecycle rules.

### Operator surfaces

**CLI first.** `ja-media status --series <id>` prints the matrix;
`ja-media run --through <stage> --series <id> [--episode <ep>]` executes the
dependency closure of missing/stale stages up to a pause point on the current
machine. These exist before, and independently of, the TUI — they are the
Phase D vertical-slice deliverable and the permanent scripting surface.

**Textual TUI** (`ja-media lakehouse`) as the pane of glass:

```text
Episode │ Capture │ Binding │ LID    │ Align  │ Bundle   │ Issues
e001    │ v1 ✓    │ auto ✓  │ v3 ✓   │ v2 ✓   │ display  │
e002    │ v1 ✓    │ human ✓ │ v3 ✓   │ stale  │ —        │
e003    │ v1 ✓    │ issue   │ —      │ —      │ —        │ ambiguous ep
```

One DuckDB query fills the matrix (the kernel's staleness query, not TUI
logic). Actions call the same kernel functions the CLI calls: `[r]un` next
eligible stage for the selected row (subject to local platform capability),
`[b]ind` / issue resolution in a modal, `[i]ssues` for the review queue
including consistency findings. "Walk to a pause point" is arrowing down,
pressing `r`, and quitting when something needs thought.

Module budget — honest, based on the existing `subsync/tui.py` (431 lines
for a simpler surface) and respecting the 300-line soft limit per file:

```text
packages/data/src/ja_media_data/lakehouse/
  schema/ (SQL files)            —    checked-in DDL
  catalog.py          ~80 lines  —    attach, config, apply_schema
  status_query.py     ~180 lines —    staleness/eligibility/consistency SQL
  run_log.py          ~60 lines  —    run records, log capture to Garage
  stages.py           ~150 lines —    stage runner protocol, idempotent commit

packages/frontend/src/ja_media_frontend/lakehouse/
  tui.py              ~200 lines —    App, matrix, bindings, refresh
  actions.py          ~100 lines —    thin adapters onto kernel functions
  issue_browser.py    ~80 lines  —    ModalScreen for issues + findings
  cli.py              ~80 lines  —    status / run entrypoints
```

Total ~930 lines against ~800 removed — roughly a wash in code volume, but
the removed lines were framework adaptation (ORM models, repository
plumbing, asset decorators, sensor) and the added lines are the domain
itself (staleness semantics, run history, the operator surface).

## What survives

Tool-agnostic domain logic, unchanged except persistence calls:

- `packages/core/src/ja_media_core/bronze.py` — typed manifest contract.
- `packages/data/src/ja_media_data/episode_resolution.py` — PTN + AniList
  matching, episode-count bounds, evidence. (Pure; no changes.)
- `packages/data/src/ja_media_data/resolution_service.py` — resolver
  orchestration (persistence calls swapped in Phase C).
- `packages/data/src/ja_media_data/resolution_evidence.py`,
  `diagnostics.py`, `episode_metadata.py`.
- `packages/data/src/ja_media_data/bronze_store.py` — boto3 Garage adapter;
  the bronze read path is unaffected.
- The 100-capture fixtures, spike report, and idempotency tests (adapted to
  DuckLake).
- The plan-doc contracts: vocabulary (`00`), bronze contract (`01`), bundle
  contract (`06`), invalidation vocabulary (`05`), packed datasets (`08`).

## What is removed

- `packages/data/src/ja_media_data/models.py`, `repository.py`,
  `ledger_types.py` (where fully replaced), `database.py`.
- `packages/data/migrations/` (Alembic; exactly one migration exists today,
  which is why now is the cheap moment).
- `packages/data/src/ja_media_data/episode_assets.py`, `bronze.py` (the
  Dagster sensor — replaced by a scan script), `definitions.py`.
- Dependencies: `dagster`, `dagster-dg-cli`, `dagster-webserver`,
  `sqlalchemy`, `alembic`. (`psycopg` remains for the catalog connection;
  `boto3` remains for bronze.) Added via `uv add`: `duckdb`; the `ducklake`
  and `postgres` DuckDB extensions install at attach time.
- `[tool.dg]` sections in `packages/data/pyproject.toml`.
- `deploy/dagster/` (compose, dagster.yaml, workspace.yaml, Dockerfile) and
  the Dagster Postgres database/principal.
- Decide `deploy/metaflow/`'s fate in the same pass — this plan standardizes
  on "no orchestrator," and a second dormant one should not linger
  unexamined.

## Migration phases

### Phase A — DuckLake-on-Garage spike

Prove the substrate against this Garage before building anything on it:

1. `uv add duckdb` in `packages/data`. Attach
   `ducklake:postgres:<catalog dsn>` with `DATA_PATH` on the Garage
   endpoint, path-style addressing, credentials via DuckDB secrets loaded
   from the environment (never printed).
2. Exercise: `CREATE TABLE`, appends (including small appends to observe
   data inlining and flush), `ALTER TABLE ADD COLUMN`, time-travel reads,
   snapshot expiry + file cleanup, concurrent appends from two machines.
3. Verify recovery: `pg_dump` the catalog, destroy and restore it, confirm
   tables read correctly; document the backup step in the runbook.

**Gate:** all of the above work against Garage; the recovery drill succeeds;
any Garage incompatibility is found now, at zero sunk cost.

### Phase B — schema and views

1. `packages/data/schema/` SQL files defining the identity tables
   (`bronze_captures`, `episode_hints`, `episode_bindings`,
   `episode_resolution_issues`) matching the contracts in
   `03-partitions-identity-and-binding.md`, minus `dagster_run_id`
   (replaced by nullable `run_source`), plus `run_log`.
2. `current_bindings` and `consistency_findings` views per the semantics
   above.
3. Tests for the currency semantics: accept, reject-without-replacement,
   supersede, correction move, race artifact detection — against fixtures.

**Gate:** the rejection-without-replacement case returns *unbound*; all
transition tests pass; `apply_schema` is idempotent from a clean catalog.

### Phase C — port the resolver

1. Replace `LedgerRepository` calls in `resolution_service.py` with DuckLake
   appends plus the pre-commit policy check.
2. Replace the Dagster bronze sensor with a scan script: list Garage
   manifest markers, upsert `bronze_captures` (rebuildable cache).
3. Port idempotency/conflict tests.

**Gate:** `ja-media-data resolve-sample --limit 100 --apply` reproduces the
baseline — 52 accepted, 48 quarantined, same reasons — against DuckLake.

### Phase D — execution kernel + vertical slice (the real proof)

Implement the kernel and prove the execution contract on one real slice,
**portable audio + LID**, CLI only:

1. `status_query.py` first: staleness/eligibility for the slice, tested
   against fixtures with deliberately stale and superseded inputs.
2. Stage runners with scratch-then-commit, `run_log`, idempotent re-run.
3. `ja-media status --series <id>` and
   `ja-media run --through lid --series <id>`.
4. Demonstrate, in order: bounded selection; `--through` walking the
   dependency closure; a mid-run kill leaving a visibly abandoned `run_log`
   row and no partial commit; re-run to completion **on a different
   machine**; a recipe bump marking downstream results stale; the status
   matrix rendering all of it truthfully.

**Gate:** every demonstration passes. This gate exists because the kernel —
not the TUI and not the storage — is where this plan could actually fail.
No TUI work begins until it holds.

### Phase E — Textual TUI

Build `ja-media lakehouse` over the proven kernel: matrix, detail panel,
issue browser (including consistency findings), light actions.

**Gate:** the operator opens the TUI on the laptop, sees a series, runs the
next light stage for a stale episode, resolves one issue, and no hash was
copied and no list command was run. Heavy-stage rows correctly show as
runnable-elsewhere on a machine lacking the capability.

### Phase F — remove Dagster and the old substrate

Delete the files and dependencies listed under "What is removed," including
`deploy/dagster/`; drop the Dagster database; update
`docs/plans/bronze-media-and-publication/` docs to reference this substrate.

**Gate:** no Dagster/SQLAlchemy/Alembic imports anywhere; Postgres holds
only the DuckLake catalog; CI green.

### Phase G (optional) — dbt

`dbt-duckdb` over the DuckLake catalog for tests, documented lineage, and a
transformation registry. Genuinely optional: with ~12 tables and one
operator, checked-in SQL plus the consistency views may remain sufficient
indefinitely. Adopt only if the SQL surface grows past what the schema
directory keeps legible, and adopt incrementally.

## Alternatives considered

- **Keep Postgres as the domain store, add a planner/runner on top.** Solves
  the coordination pain and keeps enforced constraints, but retains the
  ORM/migration stack, retains *two copies* of every silver table (live rows
  plus the required Parquet exports) with a synchronization pipeline between
  them, and grows the eventual migration surface every month it persists.
  The constraint benefit is real but narrow — two uniqueness rules on one
  table — and is priced into this plan as detect-and-repair plus a
  documented fallback.
- **Delta Lake (alone or stacked).** Stacking under DuckLake is not a real
  architecture — they are competing formats with incompatible snapshot
  models. Delta alone would drop the Postgres dependency but makes commit
  safety depend on S3 conditional writes, which Garage does not currently
  guarantee, and no external Delta consumer (Spark, Databricks, Polars)
  exists in this system to justify the log protocol.
- **Plain immutable Parquet + manifests, no table format.** The strongest
  minimal option and the repo's stated default DMZ, but it lacks
  transactional multi-machine appends, schema evolution, and time travel —
  all of which this workflow uses. DuckLake is that option plus a catalog
  the infrastructure already runs.
- **Prefect (or another orchestrator).** Reduces some Dagster friction but
  reintroduces a generic abstraction layer and an execution service, and
  still would not render the episode × stage matrix. The kernel this plan
  owns is smaller than a Prefect integration.

## Open questions to validate before committing

1. **Phase A is the question.** DuckLake + Garage compatibility, data
   inlining behavior, snapshot cleanup, and the recovery drill are all
   front-loaded into a spike with zero sunk cost behind it.
2. **Catalog backup cadence.** Confirm the existing Postgres backup regimen
   covers the catalog database, or add it; the catalog is load-bearing.
3. **Staleness query complexity.** If the Phase D prototype shows the
   fingerprint cascade is materially harder than budgeted, pause and
   reassess before the TUI — that would be evidence the kernel wants more
   structure, and it should be reviewed rather than absorbed silently.
