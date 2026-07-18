# Data layer and operator workbench architecture

This guide is the starting point for contributors working in `packages/data`.
It explains the vocabulary, storage boundaries, execution model, operator UI,
and the failure cases that shaped them. It is intentionally more explanatory
than an API reference: the difficult part of this package is not individual SQL
statements, but maintaining an honest relationship between durable products,
execution attempts, and what the operator sees.

The repository-wide architectural rules in [`../../AGENTS.md`](../../AGENTS.md)
still apply. In particular, data contracts should outlive model/runtime choices,
and new services should not be introduced merely to query internal lake data.

## The short mental model

```text
Bronze evidence
    ↓
Silver compilers and human decisions
    ↓
Gold projections
    ↓
Applications
```

Within the data package:

```text
target request
    ↓ backward planning
global pipeline run
    ↓
stage checkpoint → atomic DuckLake transaction → materialization head
    ↓
next independently committed checkpoint
```

The operator workbench is a read model over those facts:

```text
last committed output
+ producing run/checkpoint
+ latest attempt
+ currency relative to current dependencies
```

A global run is not one database transaction. A stage checkpoint is.

## Why this structure exists

The first operator slice exposed a misleading but common design failure. The UI
showed the latest table for every stage, labeled each existing table
“materialized,” and displayed the final canonical product above them. If an
upstream stage advanced while a downstream stage failed, the screen looked like
one successful coherent pipeline even though it contained outputs from several
executions.

Consider two attempts:

```text
Run A: stage 1 succeeds → stage 2 succeeds → stage 3 succeeds
Run B: stage 1 succeeds → stage 2 fails
```

The correct live workspace after Run B is:

| Stage | Committed output | Latest checkpoint | Currency |
| --- | --- | --- | --- |
| 1 | Run B | Run B succeeded | current |
| 2 | Run A | Run B failed | stale input |
| 3 | Run A | Run A succeeded | transitively stale |

Three independent facts are needed:

1. **Output lineage:** which successful checkpoint produced the committed rows?
2. **Execution history:** what happened the last time this stage was attempted?
3. **Currency:** were the committed rows built from the dependency heads that
   are current now?

Conflating these facts produces attractive but false status displays. Phase 2
therefore makes them separate durable concepts and separate UI fields.

## Domain vocabulary

### Product

A product is a durable typed result with a semantic identity. Examples include:

- `episode_resolution`: resolver proposals and quarantined evidence;
- `accepted_bindings`: proposals admitted by an automatic policy;
- `canonical_inputs`: selected episode inputs and their subtitle objects; and
- `subtitle_lid`: language evidence for canonical subtitle inputs.

A logical product may use more than one physical table when the row grains
differ. `canonical_inputs`, for example, owns:

- `canonical_episode_inputs`, one row per selected episode locator; and
- `canonical_subtitle_inputs`, zero or more subtitle rows per selected episode.

That is one product contract with two relations, not thirteen per-episode S3
objects and not one global Parquet file for the entire pipeline.

### Stage

A stage is executable code that transforms declared input products into an
output product. Stage declarations live in
[`src/ja_media_data/operator/phase_d_registry.py`](src/ja_media_data/operator/phase_d_registry.py).

The registry, rather than a template or route, owns dependency structure.

### Recipe

A recipe is the named, versioned behavior of a stage. Its revision participates
in the build key. Changing behavior without changing the recipe revision makes
the change invisible to automatic staleness detection; recipe revision is
therefore a data contract, not decorative release metadata.

`--force-from` exists for intentional recomputation when the declared revision
has not changed, but it is not a substitute for versioning durable behavior.

### Target

A target is an operator-facing sink or useful pause point. The planner walks
backward from a target through stage dependencies. It is not a hard-coded route
through a linear pipeline.

Examples in the current registry are `accepted-bindings`, `canonical-inputs`,
and `subtitle-lid`.

### Campaign

A campaign is saved operator intent plus the lens used to inspect that intent.
It binds:

1. a target;
2. a scope;
3. recipe choices; and
4. an optional stop boundary.

The current `canonicalization-gate` campaign is a corpus-scoped intent to build
canonical inputs, presented through a domain-specific binding desk.

A campaign is **not**:

- a database transaction;
- a mutable percentage-complete record;
- a scheduler;
- one execution run; or
- a cached copy of all stage rows.

Opening a campaign reconstructs a small live read model from product heads,
stage checkpoints, control-plane revisions, and bounded product pages.

### Pipeline run

A pipeline run is one operator dispatch: for example, “build subtitle LID” or
“recompute from automatic acceptance through canonicalization.” Operators refer
to it by a monotonically increasing `Run #N`; the opaque UUID remains an
internal identity behind the information disclosure. It owns an ordered set of
stage checkpoint records.

The CLI creates global runs in:

- [`src/ja_media_data/pipeline_cli.py`](src/ja_media_data/pipeline_cli.py) for
  registered target closures; and
- [`src/ja_media_data/cli.py`](src/ja_media_data/cli.py) for an applied resolver
  pass.

### Stage checkpoint

A stage checkpoint records what one run did at one stage:

```text
running → reused | succeeded | failed
```

- `reused` points at an existing current materialization.
- `succeeded` points at a newly committed or newly validated materialization.
- `failed` has no new materialization. The previous committed output remains.

A stage attempt ID identifies the actual execution. The global run ID groups
checkpoints initiated by one dispatch.

### Materialization

A materialization is the durable metadata identity for one committed or
revalidated product head. It records:

- product and scope;
- materialization ID;
- producing stage attempt;
- recipe revision;
- output fingerprint;
- structural build key;
- exact input materialization IDs and semantic fingerprints;
- control-plane revisions; and
- DuckLake snapshot ID.

Materialization metadata is append-only. The current head is the newest record
for `(target, scope)`. Older metadata is retained so lineage and historical
views do not depend on today’s head.

## Global runs made from local checkpoints

The model is deliberately neither “every stage is unrelated” nor “the entire
run is atomic.” It is a global run composed from independently atomic stage
commits.

Suppose the operator runs:

```sh
uv run ja-data run subtitle-lid --force-from accepted_bindings
```

The dispatcher creates one `pipeline_runs` row. It walks the target closure in
dependency order:

```text
accepted_bindings → canonical_inputs → subtitle_lid
```

Each checkpoint commits independently. If canonicalization succeeds but LID
fails while reading a subtitle object:

```text
accepted_bindings  succeeded/reused
canonical_inputs   succeeded and becomes the new head
subtitle_lid       failed; old head remains
global run         failed
```

This is useful partial progress, not a botched distributed transaction. The UI
must show the new canonical head, the old LID head, and the failed LID attempt.

The commit code is in
[`src/ja_media_data/pipeline_repository.py`](src/ja_media_data/pipeline_repository.py).
The Phase D compiler entrypoints are in
[`src/ja_media_data/phase_d.py`](src/ja_media_data/phase_d.py).

### Why not make the entire run atomic?

Future stages may take hours, run on different machines, require a human gate,
or fan out across many items. Holding one transaction across that work would be
impossible or operationally harmful. Successful intermediate products are
valuable checkpoints and should survive later failures.

### Why have a global run at all?

Without it, an operator action that considers five stages creates five unrelated
log entries. The system cannot explain which stages were reused, which were
attempted, where execution stopped, or what workspace the action left behind.
The global run supplies correlation; local checkpoints supply durability.

## DuckLake’s role

DuckLake owns durable automatic products and execution metadata. Its data files
live as Parquet on Garage-compatible S3 storage, while its transactional catalog
lives in PostgreSQL. DuckDB is the embedded query/compiler process.

Every committed DuckLake transaction creates a catalog snapshot. A successful
stage replacement transaction is therefore a natural local checkpoint. Phase 2
records the snapshot associated with each materialization and the terminal
snapshot left by each global run.

Relevant primary documentation:

- [DuckLake transactions](https://ducklake.select/docs/stable/duckdb/advanced_features/transactions)
- [DuckLake snapshots](https://ducklake.select/docs/stable/duckdb/usage/snapshots)
- [DuckLake time travel](https://ducklake.select/docs/stable/duckdb/usage/time_travel)
- [DuckLake snapshot expiration](https://ducklake.select/docs/stable/duckdb/maintenance/expire_snapshots)
- [DuckLake architecture/specification](https://ducklake.select/docs/stable/specification/queries)
- [DuckDB concurrency](https://duckdb.org/docs/stable/connect/concurrency)

DuckLake supplies transactional snapshots; it does not supply our notions of
target, recipe, campaign, global run, or stage dependency. Those semantics live
in the execution kernel.

### Time travel

The operator defaults to the current workspace. Run detail pages link to the
workspace left by that run. Historical reads use DuckLake table references such
as:

```sql
SELECT *
FROM canonical_episode_inputs AT (VERSION => 42);
```

The helper in
[`src/ja_media_data/lakehouse/time_travel.py`](src/ja_media_data/lakehouse/time_travel.py)
constructs internal trusted table references.

A historical run view is not an invented atomic reconstruction. It is the
actual mixed-generation catalog snapshot after the run’s last checkpoint.

Snapshots referenced by retained runs must not be expired. No automatic
retention job is implemented yet; snapshot expiration remains an explicit
maintenance action until a retention policy is designed and tested.

## PostgreSQL control plane

Human binding overrides are small concurrent decisions with uniqueness
invariants. They live in ordinary PostgreSQL rather than in a replaceable lake
table. PostgreSQL enforces one current head per locator and one current locator
per assigned capture.

The implementation is in
[`src/ja_media_data/binding_overrides.py`](src/ja_media_data/binding_overrides.py),
with schema management in
[`src/ja_media_data/binding_schema.py`](src/ja_media_data/binding_schema.py).

Phase 2 adds a monotonic `binding_overrides` revision. An effective decision
change advances it in the same PostgreSQL transaction that retires the previous
head and appends the new head. A duplicate no-op decision does not advance it.

The revision serves three purposes:

1. exact canonicalization build inputs;
2. cache keys; and
3. reconstruction of overrides active in a historical run view.

Wall-clock timestamps are intentionally not used as concurrency or cache tokens.

## Fingerprints, build keys, lineage, and staleness

These concepts answer different questions.

### Output fingerprint

The output fingerprint identifies semantic product content. If an upstream run
is repeated and produces identical effective output, the fingerprint remains
the same. Downstream products should not become stale merely because a timestamp
or run ID changed.

### Materialization ID

The materialization ID identifies an exact committed/validated checkpoint. It
answers lineage questions such as “which acceptance checkpoint did this
canonical product consume?”

### Build key

The build key answers whether a product was built or validated against the
dependency heads current at a particular moment:

```text
SHA-256(
    product identity,
    recipe revision,
    declared input semantic fingerprints,
    declared control-plane revisions
)
```

Input metadata records both materialization IDs and fingerprints:

```json
{
  "accepted_bindings": {
    "materialization_id": "materialization-accepted_bindings-…",
    "fingerprint": "…"
  },
  "bronze_captures": {
    "materialization_id": "materialization-bronze_captures-…",
    "fingerprint": "…"
  },
  "binding_overrides": {
    "revision": 17
  }
}
```

Materialization IDs preserve exact lineage. Semantic fingerprints prevent an
identical rerun from invalidating downstream work.

### Who decides staleness?

The server-side currency evaluator in
[`src/ja_media_data/operator/currency.py`](src/ja_media_data/operator/currency.py)
does. It combines:

- dependency declarations from
  [`operator/stage_contracts.py`](src/ja_media_data/operator/stage_contracts.py);
- current materialization heads;
- the registered recipe revision; and
- relevant control-plane revisions.

It returns product currency independently from execution state:

```text
missing
current
stale_recipe
stale_input
stale_override
```

The planner and UI consume that evaluator. Jinja templates never infer
staleness from timestamps or status labels.

Directly mutating a product table without advancing its materialization head is
outside the execution contract and is deliberately invisible to staleness
evaluation. All durable writers must commit product rows and metadata together.

## Current workspace versus historical run view

The current campaign screen is a live operational view. Different stages may
legitimately show outputs from different global runs. Each stage card therefore
shows:

```text
COMMITTED OUTPUT
    materialization ID
    producing global run
    producing stage attempt
    current/stale state

LATEST CHECKPOINT
    global run
    reused/succeeded/failed state
    elapsed time and failure
```

The conclusion product at the top identifies its own materialization, run, and
attempt. If upstream heads advance, the product remains inspectable but is
marked stale. It is never silently relabeled as the conclusion of a newer run.

The run detail page shows the ordered local checkpoints and links to a
historical workspace view at the run’s terminal DuckLake snapshot.

## Query and cache architecture

The first implementation reconstructed the complete campaign for every HTMX
request. Expanding a three-row product preview to thirteen rows performed catalog
attachment, stage summaries, counts, proposal joins, and Python slicing. That
was an application design bug, not an inherent DuckLake cost.

Phase 2 uses independently keyed projections.

### Campaign state token

[`operator/cache_keys.py`](src/ja_media_data/operator/cache_keys.py) reads the
two authoritative workspace heads:

- the current DuckLake snapshot ID, which advances for every committed
  automatic-product or execution-metadata transaction; and
- the PostgreSQL override revision, which advances for every effective human
  binding decision.

Together they provide exact invalidation—not a time-to-live guess—without
scanning product rows, reconstructing the campaign, or reading S3 objects.

### Product page

The canonical product endpoint keys a DTO by durable campaign heads, filter,
offset, and limit. [`operator/canonical_product.py`](src/ja_media_data/operator/canonical_product.py)
pushes pagination into bounded SQL projections and reads only locator keys,
aggregates, and the selected canonical rows for that page.

It does not rebuild stage cards or campaign counters.

### Candidate evidence

Candidate payloads have a separate endpoint and cache key:

```text
proposal head
+ acceptance head
+ override revision
+ locator
+ historical snapshot, if selected
```

They are fetched only when the operator expands one locator.

### Stage result page

Stage results are keyed by the selected materialization/snapshot, stage, filter,
offset, and limit. SQL applies `LIMIT` and `OFFSET`; the server does not load an
entire product and slice it in Python.

### Cache implementation

[`operator/cache.py`](src/ja_media_data/operator/cache.py) is a thread-safe,
bounded, process-local LRU storing typed DTOs. It contains no cursors,
connections, credentials, or correctness state.

Cache invalidation is identity-based: a changed durable head produces a new
key. No invalidation bus or mutable “dirty” flag is needed. Old entries age out
under the size bound.

Historical snapshot keys are immutable and particularly safe to cache.

## Persistent connection lifecycle

DuckDB extension loading, DuckLake attachment, PostgreSQL connection setup, and
S3 secret registration are heavyweight initialization. They must not occur per
HTTP request.

FastAPI lifespan owns an `OperatorRuntime`:

```text
OperatorRuntime
├── RepositoryPool (two attached repositories)
└── ProjectionCache
```

Startup in [`operator/http/app.py`](src/ja_media_data/operator/http/app.py):

1. load the layered environment;
2. apply additive DuckLake and PostgreSQL schemas once;
3. open two fully attached repository contexts; and
4. create the bounded projection cache.

Requests borrow one repository exclusively from the LIFO pool through
[`operator/http/dependencies.py`](src/ja_media_data/operator/http/dependencies.py).
They return it without closing either underlying connection.

Shutdown closes the pool and clears the disposable cache.

The pool is intentionally two connections. This is a local, usually
single-operator application; a configurable general-purpose pooling subsystem
would add machinery without a measured requirement. Each borrowed DuckDB
connection has only one user at a time.

## Technology stack

### Durable storage

- **DuckLake:** transactional lakehouse table/snapshot metadata.
- **Garage/S3-compatible object storage:** Parquet data files and Bronze media
  manifests/artifacts.
- **PostgreSQL:** DuckLake catalog metadata plus a separate ordinary schema for
  human override heads and revisions.

### Query and compilation

- **DuckDB:** embedded SQL execution and product compilation.
- **Python 3.13:** compiler and application implementation.
- **Pydantic:** strict immutable DTO and planner contracts.
- **psycopg 3:** direct PostgreSQL control-plane access.

### Operator application

- **FastAPI/Starlette:** loopback HTTP and lifespan ownership.
- **Jinja2:** server-rendered pages and fragments.
- **HTMX:** bounded lazy navigation and fragment replacement.
- **small static JavaScript:** carousel controls and already-loaded candidate
  row collapsing; no client-side application state store.

### Tooling

- **uv:** dependency environments, commands, and tests.
- **pytest:** unit and integration tests.

The package deliberately does not introduce React, a separate frontend build,
an ORM, an orchestration service, or a scheduler for the current requirements.

## Source map

| Area | Primary files |
| --- | --- |
| DuckLake connection/schema | `lakehouse/catalog.py`, `schema/*.sql` |
| Product repository | `lakehouse/repository.py`, `pipeline_repository.py` |
| Human override control plane | `binding_overrides.py`, `binding_schema.py`, `postgres_schema/*.sql` |
| Phase D compilers | `phase_d.py`, `canonicalization.py`, `pipeline_types.py` |
| Product/stage registry | `operator/registry.py`, `operator/phase_d_registry.py`, `operator/stage_contracts.py` |
| Planner and currency | `operator/planning.py`, `operator/status.py`, `operator/currency.py` |
| Campaign read model | `operator/application.py`, `operator/campaigns.py`, `operator/models.py` |
| Run history | `execution.py`, `operator/run_history.py` |
| Product projections | `operator/canonical_product.py`, `operator/stage_results.py` |
| Pool/cache | `operator/runtime.py`, `operator/cache.py`, `operator/cache_keys.py` |
| HTTP/UI | `operator/http/` |
| CLI | `cli.py`, `pipeline_cli.py`, `operator/cli.py` |

## Schema contracts

### `pipeline_runs`

One row per global dispatch. `terminal_snapshot_id` is the actual workspace
snapshot left after its final stage checkpoint, regardless of success or
failure. `run_number` is the monotonically increasing operator handle; `run_id`
is the stable internal UUID used by links and joins. The singleton
`pipeline_run_counter` row is advanced in the same transaction that inserts a
run, so concurrent manual dispatches cannot receive the same number.

### `run_stage_checkpoints`

One row per stage considered by a global run. It records ordering, disposition,
attempt ID, recipe/build inputs, materialization result, timing, and failure.

### `materializations`

Append-only current and historical product heads. A new record may represent:

- newly written semantic output; or
- revalidation of identical output against a new structural build key.

The latter is important when upstream metadata changes but effective downstream
content remains identical.

### `binding_overrides` and `control_revisions`

Append-only human decision history with retired current heads, uniqueness
indexes, and an exact monotonic global revision.

## Failure and recovery rules

1. Product rows and their materialization record are committed in the same
   DuckLake transaction.
2. A stage failure never deletes or partially replaces its previous product.
3. Earlier successful checkpoints in the same global run remain committed.
4. A global run failure records the exception after the failed product
   transaction has rolled back.
5. Opening the current workspace shows committed heads plus the latest failed
   attempt; it does not hide failure and does not discard good prior output.
6. Opening a historical run uses its terminal snapshot and recorded override
   revision.
7. Cache entries are never authoritative and may always be discarded.

There is a narrow crash window between a successful product commit and attaching
the returned DuckLake snapshot ID to its materialization metadata. The product
and materialization identity are already committed; a missing snapshot ID is a
recoverable metadata condition rather than product loss. Before automated
snapshot expiration is introduced, add and test a repair command that resolves
such materializations from DuckLake commit history.

## Current deliberate limitations

### Corpus commit granularity

The implemented Phase D products replace complete corpus tables. A series
filter in the UI is a read projection, not a series-scoped materialization.
Partial series reruns require an explicit partitioned commit contract and must
not be faked with partial deletes inside a corpus-scoped product.

### Manual dispatch

There is no scheduler, sensor, worker queue, or daemon orchestrator. Operators
run targets explicitly. The durable kernel makes stopped work inspectable and
reusable without claiming to schedule it.

### Read-only workbench mutations

The current web workbench is read-only. Human overrides are applied through the
CLI/control-plane repository. When web mutations are added, they must use the
same revisioned PostgreSQL transaction and show conflict failures explicitly.

### Snapshot retention

Time travel is implemented, but automatic expiration is not. Referenced run
snapshots must remain protected. Define retention from measured history/storage
needs before enabling cleanup.

### Stage 1 scope

An applied `resolve-sample` pass records a global resolver run/checkpoint.
Broader Bronze hydration and future resolver entrypoints must use the same
execution boundary before they become operator-visible writers.

## Contributor workflow

### Environment

The CLI loads `.env` files from repository root through the current working
directory, with more specific files winning. Do not print `.env` contents.

From `packages/data`:

```sh
uv sync
uv run ja-data apply-lakehouse-schema
```

Schema migrations are checksum protected. Never edit an applied numbered SQL
file; add the next migration.

### Inspect targets and plans

```sh
uv run ja-data targets
uv run ja-data campaigns
uv run ja-data plan canonicalization-gate
```

### Run a target closure

```sh
uv run ja-data run canonical-inputs
uv run ja-data run subtitle-lid --force-from canonical_inputs
```

Stage names use underscores in `--force-from`; target names use hyphens.

### Start the operator server

```sh
uv run ja-data web --port 8765
```

Open <http://127.0.0.1:8765/operator>.

The server is loopback-only by design. Startup performs schema verification and
opens the persistent pool before accepting requests.

### Tests

From the repository root:

```sh
uv run pytest packages/data/tests
```

The integration tests expect the disposable PostgreSQL fixture described by
the existing lakehouse development setup. Important tests include:

- identical reruns reuse current product heads;
- downstream object-read failure preserves prior complete products;
- a global run retains earlier successful checkpoints after later failure;
- historical run views return rows from the recorded DuckLake snapshot;
- override revision advances atomically and not on no-op decisions;
- HTMX product pages are bounded and candidate evidence is lazy; and
- schema migrations are idempotent and checksum protected.

When changing execution behavior, test the failure path before polishing the
success UI. The operator model exists primarily to make partial failure honest.

## Design reading

These references explain the system-design ideas used here. They are not all
implementation dependencies.

### Transactions, logs, and derived data

- Martin Kleppmann, *Designing Data-Intensive Applications*, especially the
  chapters on storage/retrieval, replication, transactions, batch processing,
  and stream processing. The useful theme here is separating source-of-truth
  state from rebuildable derived views.
- Pat Helland, [“Life beyond Distributed Transactions: an Apostate’s Opinion”](https://www.cidrdb.org/cidr2007/papers/cidr07p15.pdf).
  This motivates explicit local commits and durable progress instead of trying
  to stretch one transaction across long multi-step work.
- Hector Garcia-Molina and Kenneth Salem,
  [“Sagas”](https://www.cs.cornell.edu/andru/cs711/2002fa/reading/sagas.pdf).
  We do not implement compensating saga actions here, but the paper is useful
  background for reasoning about global work composed from local transactions.

### Content identity and reproducible builds

- [Bazel build encyclopedia: dependencies and incremental builds](https://bazel.build/basics/dependencies).
  The distinction between declared inputs and outputs is directly relevant to
  structural build keys.
- [Nix thesis](https://edolstra.github.io/pubs/phd-thesis.pdf), especially the
  treatment of pure build inputs and store identities. Our lake products are
  not a Nix store, but semantic fingerprints and explicit input heads solve a
  related reproducibility problem.

### Lakehouse and snapshot mechanics

- [DuckLake documentation](https://ducklake.select/docs/stable/)
- [Apache Iceberg specification](https://iceberg.apache.org/spec/), as
  comparative reading for snapshot-oriented table formats.
- [Delta Lake protocol](https://github.com/delta-io/delta/blob/master/PROTOCOL.md),
  as comparative reading for transaction-log-based lake tables.

DuckLake is the implemented format. Iceberg and Delta are reading material, not
an invitation to add another storage abstraction.

### UI and operational state

- Richard Cook,
  [“How Complex Systems Fail”](https://how.complexsystems.fail/). The relevant
  lesson is that operational interfaces must reveal degraded mixed state rather
  than summarize it away.
- Martin Fowler,
  [CQRS](https://martinfowler.com/bliki/CQRS.html). The workbench uses a modest
  read-model separation, not a full CQRS architecture; the article is useful for
  understanding why application DTOs need not mirror write tables.
- [HTMX documentation](https://htmx.org/docs/), particularly server-rendered
  fragments, targets, and swaps.

## Rules for extending the system

### Next gate: evidence-bound operator decisions

The next approved gate adds reusable primitives for an operator to resolve
competing canonical candidates or bindings from the stage-owned evidence view.
It is not a general workflow or approval engine.

Before implementation, settle and test the decision precondition: a submitted
choice must identify the locator and the exact proposal/canonical product head
the operator reviewed. If that evidence changed before commit, the action must
reject or require a fresh preview rather than silently applying an obsolete
choice. The durable mutation should continue through the existing PostgreSQL
`binding_overrides` transaction, including explicit unbind, while DuckLake
products remain compiler-owned.

After a successful decision, the current canonical product should become
visibly stale through the override revision already included in its build key.
The action must not silently launch recomputation. Preview, append decision,
show provenance, and expose the resulting recomputation boundary are separate
application use cases. Any broader approval inbox, remote execution, scheduler,
or destructive action needs a new measured requirement and architectural
review.

### Adding stages, products, and widgets

When adding a stage or product:

1. Define the durable product grain and provenance fields first.
2. Register the stage’s exact input products and recipe revision.
3. Compute a structural build key only from declared durable heads.
4. Commit rows and materialization metadata atomically.
5. Record the stage checkpoint under a global run.
6. Make failure preserve the previous complete product.
7. Add a bounded read projection; never load an unbounded product into a route.
8. Key cache entries by durable identities, not time-based guesses.
9. Add current and stale tests, including identical-output reruns.
10. Decide whether historical query support is meaningful for the new product.

When adding an operator widget:

1. State whether it shows committed output, an attempt, or both.
2. Display the producing run/materialization when presenting a conclusion.
3. Keep currency separate from execution status.
4. Load stage-owned details only when the operator steps into that stage/cell.
5. Push filtering and pagination into SQL.
6. Do not perform object-store reads in an ordinary page request unless the
   widget explicitly exists to inspect object contents.

If a screen cannot answer “which committed output is this, which attempt last
ran, and is the output current?”, the screen is not finished.
