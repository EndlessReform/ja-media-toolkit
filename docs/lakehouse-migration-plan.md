# Lakehouse migration: DuckLake on Garage, retire Dagster

Status: accepted and in progress; Phases A–C2 validated. Amended 2026-07-14
after Phase B/C review and 2026-07-15 to restore PostgreSQL ownership of human
binding decisions. The amendments
correct the write shape (derivations are recomputed and replaced, not
event-sourced), retain database-enforced decision invariants, and relocate the
operator surface to `packages/data`. See
"Binding product" and "Operator surfaces" below, and Phase C2 in the phases
document.

This plan replaces the retired Dagster + Alembic + PostgreSQL-domain-tables
spike with DuckLake tables on Garage, the
existing PostgreSQL instance as both DuckLake catalog and a deliberately tiny
binding-decision control plane, a small explicit execution kernel, and CLI +
Textual surfaces for the operator. dbt is an optional later phase, not a
prerequisite.

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
- **Machine output is recomputed, not accumulated.** Resolver results are
  derived DuckLake tables replaced wholesale under an input fingerprint.
- **Human binding decisions live in ordinary PostgreSQL.** The small
  `binding_overrides` relation retains decision history and uses partial unique
  indexes for active locator/capture heads. Automatic-output uniqueness is
  enforced by the whole-corpus batch writer; operator decisions use the
  transactional substrate already chosen for point consistency.
- **The operator surface is `ja-data` in `packages/data`**, next to its
  dependencies. `ja-media` (`packages/frontend`) remains the consumer CLI for
  media tools and never grows pipeline verbs.

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

**Measured:**

- The resolver works: 100 captures → 96 hints, 52 accepted bindings, 48
  quarantined with informative reasons. This is the behavioral baseline.
- Per-record Dagster assets failed concretely: conditional outputs made
  quarantine look like missing data; blocking checks turned legitimate
  quarantines into failed runs; partition mappings could not express the
  data-dependent capture→locator fan-in without duplicating Postgres
  eligibility state into a second registry. Both repairs were rejected.
- The C2 compiler reproduced 100 captures → 96 hints, 52 automatic bindings,
  and 48 quarantines; an identical second run performed zero table writes.
- PostgreSQL partial unique indexes atomically enforced competing active human
  binding decisions, while an override became visible without a DuckLake
  rebuild.
- DuckLake catalog recovery, schema evolution, time travel, and file cleanup
  worked against Garage. See `lakehouse-phase-a-spike-report.md` for the
  substrate evidence.

**Assumed (not yet measured):**

- The real size of the execution kernel and TUI (budgeted conservatively
  below, with an operator-visibility slice before stage execution is built).
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
| Web UI | Read-only Textual status surface (Phase D0) + `ja-data status` (Phase D) |

## Why the substrate changes: one copy of the data

The retired design stored silver collections as Postgres rows managed by
Alembic, **and** exported versioned Parquet snapshots to Garage for recovery
and analytics. That is two copies of
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
- **Small metadata writes remain practical.** DuckLake can inline small
  inserts in the catalog and flush them to Parquet later. Automatic identity
  products are nevertheless compiled and replaced as whole batches; human
  point decisions belong in PostgreSQL, not row-at-a-time DuckLake writes.
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
DuckLake Parquet data files                binding_overrides (ordinary PG)
worker scratch (lifecycle-reaped)

DuckLake tables (Parquet on Garage, catalog in Postgres)
────────────────────────────────────────────────────────
bronze_captures            rebuildable index, rescanned and replaced
episode_hints_auto         derived resolver claims, replaced per recompute
episode_bindings_auto      derived resolver decisions, replaced per recompute
resolution_issues_auto     derived quarantine reasons, replaced per recompute
audio_language_results     derived, recipe-versioned
subtitle_alignments        derived, recipe-versioned
episode_bundles            derived, policy-versioned
materializations           fingerprint per (target, scope) = what is current
run_log                    append-only execution history
consistency_findings       VIEW (read-side data-quality checks, in status)

Composed query projections (packages/data)
──────────────────────────────────────────
current_bindings           active PG override, else DuckLake auto row

Execution kernel (packages/data)          Operator surface (packages/data)
────────────────────────────────          ────────────────────────────────
target DAG + fingerprint staleness        ja-data status [--series <id>]
stage runners (resolve, LID, align,       ja-data run <target> [--series|--set]
  transcode, bundle) with replace-        ja-data bind / issues / targets
  or-append commits and run_log           (Textual TUI optional, after Phase D)
```

### Ownership boundaries

- **Garage owns bytes**: bronze media and manifests, silver media artifacts,
  gold bundles, DuckLake Parquet, large logs, scratch.
- **Postgres owns transactional metadata and decisions**: the DuckLake catalog
  in its guarded schema and `binding_overrides` in a separate,
  application-owned schema. Checked-in SQL owns the latter; DuckLake never
  writes it.
- **DuckLake tables own facts and derivations**: what exists, what the resolver
  claimed, what automatic result was computed, and what ran.
- **The execution kernel owns semantics**: what is stale, what is eligible,
  what running a stage means, how results commit.
- **CLI and TUI own presentation and dispatch only.** They are two views
  over the same kernel functions and contain no pipeline logic.

### Binding product: derived table plus human overrides

*(Amended 2026-07-14. The original design ported the Postgres ledger's
event-sourced shape — monotonic binding IDs, supersession pointers, a
window-function currency view, and write-time head checks — into DuckLake.
Phases B–C implemented it faithfully and the reports repeatedly observed it
"cuts against the grain": the shape re-implements, in application code,
guarantees the old substrate gave for free. The amendment replaces the shape
with one that matches what the data actually is.)*

All 52 accepted bindings in the 100-capture baseline are machine outputs, not
human decisions: they are the output of a **pure function** of (bronze
manifests, AniList metadata, recipe version) — Phase C proved determinism by
exact replay. Pure functions are recomputed, not event-sourced:

- **`episode_bindings_auto` is a derivation.** Each resolver run recomputes
  the full result and replaces the table in one DuckLake transaction,
  recording the input fingerprint in `materializations`. Re-running with
  unchanged inputs is a no-op by fingerprint comparison — no per-row
  existence probes, no semantic idempotency keys. History is DuckLake
  snapshot time travel, not rows; "recipe v3 superseded v2" is simply the
  table being v3's output.
- **PostgreSQL `binding_overrides` is the only human decision table.** A human
  accepting, correcting, or unbinding an episode advances one locator head:
  the previous row is retired and a replacement row records locator, capture
  (NULL to unbind), method, note, and database timestamp. Retired rows preserve
  history; partial unique indexes enforce one active row per locator and one
  active locator per non-NULL capture. It is a *source* in the same sense
  bronze is — written by events outside the pipeline, never rebuilt.
- **`current_bindings` is a composed query projection**: the active override
  for a locator if one exists (an unbind override masks the auto row without
  deleting anything), otherwise the auto row. A quarantined capture bound by
  override simply shows as bound; its shadowed auto issue is suppressed in
  status rather than "resolved" by a write — issue rows are derived and
  disappear when a recompute no longer produces them.

Rejection-without-replacement still returns *unbound* (the Phase B gate):
the active override for the locator is an unbind, so the projection yields no
row.

### Invariant posture: enforce where each write belongs, check across boundaries

The two uniqueness rules (one current capture per locator, one current
locator per capture) are properties of the resolver's *whole-corpus* output.
The batch writer sees the entire result before it commits, so it enforces
them in ordinary Python and quarantines conflicts as issues — a writer with a
complete view needs no row-level constraints. This is why lakehouses without
key constraints run real workloads everywhere: their writers are idempotent
batch jobs, not per-row compare-and-swap ledgers.

- **Overrides are transacted in PostgreSQL.** A short transaction serializes
  head changes, retires the old locator head, inserts the replacement, and
  lets partial unique indexes reject competing active locator/capture heads.
  There is no application-level compare-and-swap or ordered-ID choreography.
- **`consistency_findings` remains as a read-side check** surfaced in
  `ja-data status` — a data-quality test in the dbt sense (override
  referencing a vanished capture, or a later auto recompute assigning an
  overridden capture under a different locator), not a transactional backstop.
- **The cross-substrate check remains explicit.** PostgreSQL cannot foreign-key
  an override to a DuckLake capture, so append-time application validation and
  `consistency_findings` detect missing or stale capture references. That is a
  real boundary check, not an attempt to reproduce PostgreSQL concurrency in
  DuckLake.

### Schema management: DDL in git

No Alembic, but no ad-hoc laptop DDL either. `packages/data/schema/` holds
numbered, checked-in SQL files (`001_identity.sql`, `002_results.sql`, ...),
each applied transactionally through DuckLake, plus a tiny idempotent
`apply_schema` entrypoint that records applied files in a `schema_history`
table. Adding a column for LID v3 is a reviewed one-line SQL file, not a
migration module — review is preserved, the framework is removed. Clean
environments are reproducible by applying the directory in order.
`packages/data/postgres_schema/` does the same for the one application-owned
PostgreSQL relation, with separate checksum history in its control schema.

### Execution kernel

The part of orchestration this pipeline genuinely needs, made explicit and
owned as code rather than hidden inside UI actions:

- **Staleness by fingerprint** (`status_query` module). Each materialized
  target stores the hash of its exact inputs (upstream fingerprints, recipe
  file content hash, input ETags) in `materializations`; a target is stale
  when the stored hash differs from the one recomputed from current
  upstreams. Because derived tables are replaced wholesale, "current" needs
  no derivation — the table is current by construction — and stage state
  compiles to a fingerprint join rather than a cascade over decision logs.
  (The original
  plan flagged this as the hardest module; most of that predicted difficulty
  came from deriving currency out of the ledger shape, which the write-shape
  amendment removes. It is still prototyped first in Phase D, because if it
  is wrong the status matrix displays lies.)
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
  at) the CUDA box or Mac and runs `ja-data run <target>` there. Platform
  checks in the stage runners refuse work the local machine cannot do.
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
conservative:

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

*(Amended 2026-07-14: the original plan placed the pipeline CLI and TUI in
`packages/frontend` under the consumer `ja-media` entry point. That grafts
plant operation onto the consumer surface and drags duckdb/psycopg into the
frontend environment. Withdrawn.)*

**Both surfaces live in `packages/data`; the status model comes first.** A thin
read-only Textual view is deliberately built before stage execution so the
operator can sanity-check real C2 state while later phases arrive. The CLI is
`ja-data` (renamed from `ja-media-data`), and its verb set does not grow with
the pipeline:

```text
ja-data status  [--series <id>]        episode × stage matrix + findings
ja-data run <target> [--series <id> | --set <name>]
                                       walk the target's dependency closure;
                                       default scope is "everything stale"
ja-data targets / ja-data stages       introspection, rendered from the
                                       registry that executes (cannot drift)
ja-data bind / ja-data issues          human decisions and the review queue
ja-data schema apply                   checked-in DDL
```

Three rules keep the surface small as the pipeline grows to dozens of steps:

- **Targets, not routes.** `run` names a product (`abs-bundle`,
  `eval-dataset`, `webdataset`, `transcripts`); the kernel walks that sink's
  ancestry. Forks in the DAG are different sinks reusing shared upstream
  stages through fingerprints — adding a stage or target adds zero CLI
  surface.
- **Structure is code; parameters are files; membership is data.** The stage
  DAG is an ordered registry in Python (structure has behavior — fingerprint
  composition, platform checks, closure). Recipe parameters (ASR model,
  VAD thresholds, stem counts) live in per-stage TOML files validated by
  Pydantic models; the recipe file's content hash is a fingerprint input, so
  editing a recipe automatically stales exactly its downstream. Curated
  series membership lives in small named-set TOML files (or a pinned lake
  table), never in shell globs. Workflow logic never goes in YAML/TOML.
- **Default scope is the stale set.** `ja-data run abs-bundle` after a
  50-series ingest prints the discovered worklist, confirms once, and goes.
  Enumerating series on the command line is the exception, not the routine.

**Textual TUI** as the pane of glass over the same status projection:

```text
Episode │ Capture │ Binding │ LID    │ Align  │ Bundle   │ Issues
e001    │ v1 ✓    │ auto ✓  │ v3 ✓   │ v2 ✓   │ display  │
e002    │ v1 ✓    │ human ✓ │ v3 ✓   │ stale  │ —        │
e003    │ v1 ✓    │ issue   │ —      │ —      │ —        │ ambiguous ep
```

The first slice is read-only: refresh, filtering, findings, binding provenance,
and details. Unimplemented LID/alignment/publication stages render explicitly
as `not implemented`, never as pending, stale, or successful. Widgets do not
query storage directly; a UI-independent status projection fills the matrix
and later gains real fingerprint state as each stage is implemented. Mutation
and execution actions follow only after their underlying operations are proven.

Module budget — revised after the write-shape amendment; `catalog.py` is the
measured Phase B figure, not the original optimistic estimate:

```text
packages/data/src/ja_media_data/lakehouse/
  schema/ (SQL files)            —    checked-in DDL
  catalog.py          ~240 lines —    attach, config, apply_schema (measured)
  writer.py           ~100 lines —    batch replace + override append
  status_query.py     ~120 lines —    fingerprint staleness + findings
  stages.py           ~150 lines —    target registry, runners, commits
  recipes.py           ~60 lines —    TOML recipe loading + content hashing
  cli.py additions    ~120 lines —    status / run / bind / targets
```

Total ~790 lines against ~800 removed — a wash in volume, but the removed
lines were framework adaptation (ORM models, repository plumbing, asset
decorators, sensor) plus ledger-shape constraint simulation, and the added
lines are the domain itself (staleness semantics, run history, the operator
surface).

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
- The 100-capture fixtures and idempotency tests (adapted to DuckLake), with
  the validated result recorded in the phase companion.
- The durable storage, binding, invalidation, bundle, and operator contracts
  consolidated in this document and its phase companion.

## What Phase F removed

- `packages/data/src/ja_media_data/models.py`, `repository.py`,
  `database.py`, and `diagnostics.py`; `ledger_types.py` was reduced to the
  surviving C2 domain records and renamed `resolution_types.py`.
- `packages/data/migrations/` and `alembic.ini` (Alembic; exactly one migration
  existed, which is why this remained a cheap deletion).
- `packages/data/src/ja_media_data/episode_assets.py`, `bronze.py` (the
  Dagster sensor — replaced by a scan script), `definitions.py`.
- Dependencies: `dagster`, `dagster-dg-cli`, `dagster-webserver`,
  `sqlalchemy`, `alembic`. (`psycopg` remains for the catalog connection;
  `boto3` remains for bronze.) Added via `uv add`: `duckdb`; the `ducklake`
  and `postgres` DuckDB extensions install at attach time.
- `[tool.dg]` sections in `packages/data/pyproject.toml`.
- The `deploy/dagster/` Compose deployment and local image. The Dagster
  Postgres database/principal remains a user-owned infrastructure cleanup.
- `deploy/metaflow/` was reviewed and retained for the separate evaluation
  workbench proposal in `eval-workbench-design.md`; it is not part of this data
  layer and must not be used as an alternative orchestrator here.
- The Phase C DuckLake *ledger* write path is additionally reworked by Phase
  C2 (see the phases document): ordered/ULID ID generation, write-time head
  checks, and race fixtures are deleted in favor of the batch writer.

## Migration phases

The implementation sequence, fixtures, development stack, and acceptance gates
live in [lakehouse-migration-phases.md](lakehouse-migration-phases.md). Keeping
the execution checklist separate lets this document remain focused on the
architectural decision and ownership boundaries.

At a glance: Phase A proves the substrate; B defines schema and views; C ports
the resolver; C2 reshapes the write path to recompute-and-replace and restores
the PostgreSQL decision boundary (the 2026-07-14 and 2026-07-15 amendments);
D0 adds a read-only operator view; D proves the execution kernel with portable
audio + LID; E adds safe actions; F removed Dagster and the old substrate; G
optionally adopts dbt.

## Alternatives considered

- **Keep Postgres as the entire domain store, add a planner/runner on top.** Solves
  the coordination pain and keeps enforced constraints, but retains the
  ORM/migration stack, retains *two copies* of every silver table (live rows
  plus the required Parquet exports) with a synchronization pipeline between
  them, and grows the eventual migration surface every month it persists.
  The constraint benefit is real but narrow. This plan keeps precisely that
  narrow decision table in PostgreSQL while moving recomputable, analytical,
  and media-adjacent products to DuckLake.
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

## Remaining validation questions

1. **Catalog backup cadence.** Confirm the existing Postgres backup regimen
   covers the catalog database, or add it; the catalog is load-bearing.
2. **Recipe fingerprint granularity.** Content-hashing recipe files means an
   edit stales everything downstream of that stage, so recipes must be split
   per-stage rather than one mega-config. If Phase D shows file-level hashes
   are still too coarse (innocent edits triggering corpus-scale rebuild
   scares), hash the parsed, stage-relevant subset instead of file bytes.
3. **Staleness query complexity.** Largely defused by the write-shape
   amendment (currency no longer needs deriving), but the rule stands: if
   the Phase D prototype is materially harder than budgeted, pause and
   reassess before the TUI rather than absorbing it silently.
