# Data control plane and operator architecture

This is the contributor entry point for `packages/data`. It explains what the
system does, which runtime owns each fact, and where new code belongs. The key
rule is simple: **domain products and decisions are permanent; execution
frameworks and model runtimes are replaceable.**

## What the package does

The data layer turns captured anime media into reusable products:

```text
Garage bronze manifests/media
  → episode-resolution proposals and quarantine
  → automatically accepted bindings + human overrides
  → canonical episode/subtitle inputs
  → VAD / ASR / alignment / language products
  → gold consumer projections
```

The first operator campaign is `canonicalization-gate`. One checked-in
`OperatorCampaign` constructs its actual Dagster asset job and couples it to
the workbench lens that explains which captures competed, what was admitted,
which override is active, and which capture became canonical.

Bronze commit markers have two supported layouts. Legacy v1 markers contain
one `audio` object. Schema v2 markers contain an ordered `audio_tracks` array;
the canonicalization recipe deliberately selects the first track whose
`declared_language` is exactly `jpn`. The canonical episode product records the
selected bucket, object key, stream index, codec, and declared language so
later audio consumers never reinterpret the manifest. A v2 marker without a
declared Japanese track fails canonicalization rather than silently selecting
English audio. Bronze bucket and prefix are deployment configuration, not
schema identity or code constants.

## Technology stack

| Concern | Technology | Why it owns the concern |
| --- | --- | --- |
| Asset DAG, runs, step events, queues | Dagster | Commodity orchestration and durable execution history |
| Heavy-step dispatch | Dagster Celery executor + RabbitMQ | A native worker can attach later and drain queued work |
| Domain tables and snapshots | DuckLake: Parquet in Garage, catalog in PostgreSQL | Open durable products with transactional replacement and time travel |
| Human decisions | ordinary PostgreSQL | Constraints and transactions for small mutable heads |
| Operator API/UI | FastAPI, Jinja, HTMX | Domain-specific decisions and bounded read models |
| Heavy ML implementations | `envs/apple`, `envs/cuda` | Runtime/model dependencies stay outside the durable data package |

Dagster storage and the DuckLake catalog may use the same PostgreSQL server,
but they are different authorities. Code must never query Dagster's tables
directly. The workbench uses public `DagsterInstance` APIs through one gateway.

## Environments and configuration

There are exactly three environment names:

- **local** is disposable infrastructure on a contributor machine;
- **dev** is the persistent shared integration deployment; and
- **prod** is the eventual production deployment.

Do not introduce `staging` until it has a distinct workload and lifecycle. Do
not put spike or phase names in database schemas, object prefixes, images, or
environment variables. Those names outlive the experiment that created them.

`DataSettings` is the sole application configuration model. Pydantic Settings
loads process environment over an adjacent `.env.<environment>` secrets file,
then `config.<environment>.toml`, then typed defaults. Stable endpoints,
buckets, prefixes, schemas, paths, and UI URLs belong in TOML. Secrets and
framework connection strings belong in the dotenv file or service manager.
Nested overrides use Pydantic's standard names such as
`JA_MEDIA_BRONZE__BUCKET`; `JA_MEDIA_DATA_CONFIG` selects another TOML file.

Dagster's `DAGSTER_HOME`/`DAGSTER_POSTGRES_URL` and Celery's
`JA_MEDIA_CELERY_BROKER_URL` remain environment variables because those
frameworks consume them directly. Do not mirror every TOML field into a second
flat environment API.

Database names and object prefixes follow `<system>_<environment>` and
`<system>/<environment>/`. The planned shared deployment therefore uses
`ja_media_data_dev`, `ja_media_dagster_dev`, and `ducklake/dev/`; the disposable
overlay uses `*_local` and `ducklake/local/`.

## Three runtime surfaces

### Durable control plane

The Dagster webserver, daemon, code location, run storage, RabbitMQ, and the
always-on `server` worker own:

- invariant asset dependencies;
- executable campaign job selections;
- run and step status, logs, retries, cancellation, and queue state;
- dispatch to capability queues; and
- materialization events that reference domain materialization IDs.

They do not own binding decisions or reconstruct product rows.

### Operator application

FastAPI owns:

- transactional operator commands such as binding overrides;
- operator labels and domain lenses structurally attached to Dagster jobs;
- bounded DuckLake/PostgreSQL projections;
- the graph-derived pipeline spine and curated Dagster run summaries;
- links from domain rows to Dagster execution detail; and
- small caches keyed by exact domain and Dagster revisions.

FastAPI is not a scheduler or executor. Stopping it does not stop a Dagster run.
Raw logs and generic retry/cancel controls belong in Dagster rather than being
reimplemented in HTMX.

### Heavy environments

An Apple/CUDA/hosted worker receives a versioned item envelope and scoped
Garage access. It downloads declared objects, invokes the environment-owned
backend, uploads outputs under a fingerprinted staging prefix, and writes the
result marker last. It does not read DuckLake, mutate overrides, select new
work, or know the campaign DAG.

The server verifies the marker and objects, then publishes the domain product.
This keeps a laptop or ephemeral GPU from receiving catalog/control credentials
and makes a later Slurm/Runpod/Modal dispatcher a transport change rather than
a product-schema rewrite.

## Product and execution vocabulary

### Product

A product is a durable typed domain result. A logical product may own multiple
relations at different grains. `canonical_inputs`, for example, owns one
episode table and one zero-to-many subtitle table. It is not one table per
pipeline stage and not one JSON object per item.

Whole-collection products atomically replace all owned rows. Future
incremental products commit at their natural key (for example one
episode/recipe transcript) and retain a row-level current head.

### Asset and computation node

A Dagster asset names a durable product surface. A multi-asset computation may
publish several relations atomically. The graph edges exist only in
`orchestration/dagster/assets.py`; campaign files and UI templates must not
repeat them.

The UI stage card is presentation metadata for a Dagster computation node. It
labels the node and names bounded domain tables used for counts. It is not a
second executable `Stage` hierarchy.

### Campaign

A campaign is a named Dagster asset job with an operator-supported lens:

- target asset selection;
- declared scope;
- recipe/config bindings;
- optional stop boundary; and
- operator lens.

Campaign objects live in `ja_media_data/campaigns/`. Their factory creates the
actual Dagster job and its identifying tags; the same object is registered in
Dagster `Definitions` and the operator catalog. The lens declares its required
conclusion assets, and startup refuses to load if the resolved job does not
select them. There is no string job pointer or second target list in TOML.

Dagster assets remain the source of graph edges, the campaign job remains the
source of executable selection, and presentation metadata owns only human copy
and lens behavior. A campaign is not another DAG, scheduler, mutable run, or
transaction. Deployment TOML configures infrastructure; it does not define
program structure.

### Run

A run is one Dagster execution of a campaign job. The read-only E2.1 UI uses
Dagster's monotonic run-record storage ID as `Run #N`; the UUID remains the
cross-system identity. If the operator application later launches runs, it may
attach human context as Dagster tags; it must not create another run table or
copy step state.

A run is not atomic across stages or items. If 93 episode products commit and
item 94 fails, Dagster truthfully reports a failed run while DuckLake truthfully
reports 93 advanced item heads. The workbench joins both facts.

### Materialization

The domain `materializations` relation records the current and historical
identity of a product commit:

- target and scope;
- content fingerprint;
- recipe revision and structural build key;
- exact input materialization/fingerprint heads;
- producing Dagster attempt identity;
- DuckLake snapshot; and
- materialization ID referenced by Dagster metadata.

Dagster events are not sufficient to interpret DuckLake after Dagster storage
loss, and DuckLake materializations do not replace Dagster run history.

### Currency and execution status

These are deliberately separate:

- **currency** asks whether a committed product was built from current inputs,
  recipe, and decision revision;
- **execution status** asks what happened during a Dagster run or step; and
- **lineage** asks which successful run produced the current domain head.

A failed rerun does not erase an older head. A later upstream commit may make
that older head stale. The UI must show both rather than label every non-empty
table “materialized.”

Item eligibility belongs to the owning product's DuckLake query. Collection
Dagster data versions cannot decide whether one episode is current for a given
model, bias set, and recipe.

## Storage contracts

### Garage

Garage owns immutable bronze media/manifests, DuckLake Parquet, durable media
outputs, temporary worker staging, and compact replay bundles for protected
runs. Celery messages never contain media bytes.

One compressed frozen selection may temporarily identify a large run. An item
request normally lives only in the queue and an ephemeral local file. A result
marker is temporary commit protocol, not permanent business history.

### DuckLake

DuckLake owns rebuildable and queryable domain products, materialization
lineage, and compacted `worker_handoff_items`. Current heads remain usable when
Dagster or FastAPI is unavailable. Historical product views use DuckLake
snapshots referenced by Dagster materialization metadata.

The historical `pipeline_runs` and `run_stage_checkpoints` relations remain in
old schema migrations because applied migrations are immutable. E2.1 has no
writer or reader for them; they are inert legacy tables, not a compatibility
surface.

### PostgreSQL decision schema

`binding_overrides` and `control_revisions` own active human decisions and
exact cache invalidation. Partial unique indexes enforce one active locator
head and prevent one capture from being bound to two locators.

### Worker handoff retention

Individual staging objects are deleted only after:

1. the worker marker and every output object verify;
2. the domain product commits;
3. the handoff row compacts into DuckLake; and
4. the protected run has a verified replay bundle when required.

Protected runs are active runs, explicitly pinned runs, the newest N terminal
runs, and the newest K successful runs for each active campaign/contract
generation. This is run-count retention, not a wall-clock grace period.

## Cache design

FastAPI creates a small pool of attached DuckLake repositories and one Dagster
gateway during lifespan startup. Routes borrow a repository; they do not
initialize DuckDB, S3, or Dagster per request.

Cache keys are intentionally narrow:

```text
campaign spine = campaign revision + domain snapshot/override + Dagster cursor
product page   = materialization/snapshot + override + filter + offset + limit
candidate row  = proposal head + override + locator
run summary    = Dagster run ID + item-handoff head
```

Paging from three rows to fifty is one product query. It does not load run
history or reconstruct the campaign closure. Candidate expansion reads one
locator only.

## Module map and dependency direction

```text
products/                 framework-neutral compilers, models, commits, lineage
storage/                  bronze, PostgreSQL decisions, compacted handoffs
lakehouse/                DuckLake attachment, schema, time travel
migrations/ducklake/      immutable DuckLake product migrations
migrations/control_postgres/ immutable transactional-decision migrations
orchestration/dagster/    assets, jobs/definitions, metadata, gateway, queues
workers/                  envelopes and transport-neutral environment invocation
operator/models/          stable UI/API DTOs
operator/*.py             bounded projections and application use cases
operator/http/            FastAPI, Jinja, HTMX, CSS
campaigns/                Dagster job objects bound to operator presentation
```

Allowed dependency direction:

```text
Dagster adapters → products/storage
operator application → Dagster gateway + products/storage
environment worker → worker contract + runtime backend
products/storage ↛ Dagster/FastAPI/Celery
worker invocation ↛ Dagster/FastAPI/Celery/catalog
```

Do not introduce `BaseStage`, a string-keyed campaign registry, a second
planner, or a second execution ledger. The small `CAMPAIGNS` composition root
registers actual Dagster job objects; it is not an execution registry. New
business logic belongs with the product that owns its output. New Dagster code
should be thin translation and resource assembly.

## Local, shared development, and production

### Unit and integration tests

Inject repositories and `DagsterInstance.local_temp()`. Execute real asset jobs
in process when distribution is irrelevant. These tests prove graph, product,
failure, and UI contracts; they do not prove delayed native claiming.

### Local development

Compose runs PostgreSQL, MinIO, RabbitMQ, Dagster webserver/daemon/code
location, and the server worker. The FastAPI workbench runs from
`packages/data`. A native Apple worker starts later from the checkout when the
operator is ready to drain ML work. See [DAGSTER.md](DAGSTER.md).

### Shared development and production

The durable stack runs on the server. A workstation connects over a private
network using distinct least-privilege queue and Garage credentials. The
Dagster Celery dispatcher currently also needs a Dagster-storage credential;
that credential is dispatcher plumbing and must never enter the environment
work envelope. The model command itself needs only request, Garage, scratch,
and runtime/model access.

Remote deployment and infrastructure operation remain user-owned under the
repository rules.

The first shared DEV topology deliberately separates volatility:

- persistent: PostgreSQL databases, RabbitMQ, Dagster webserver, and daemon;
- existing external data plane: Garage and first-party API gateway;
- replaceable: code location, `server` worker, and FastAPI workbench; and
- on demand: native Apple/CUDA workers.

Ordinary Python/template changes must require only restart of a replaceable
process. Dependency or base-image changes may rebuild its image. The persistent
webserver and daemon must not contain application code or require rebuilding
when a product compiler changes.

## Failure semantics

### Episode-resolution outcome reasons

The `episode-filename-v2` recipe uses literal reason codes that name the failed
invariant. A quarantine is a successful, non-publishing classification; its
`kind` (`ambiguous` or `invalid`) is the broader review category, while its
reason lists the exact conflicting or invalid inputs that caused that classification.

| Reason | Meaning |
| --- | --- |
| `bronze_manifest_failed_schema_validation` | The committed Bronze JSON could not be parsed as a supported manifest. |
| `filename_contains_multi_episode_range` | The filename explicitly names an episode range such as `Ep03-04`. |
| `filename_has_no_recognizable_episode_number` | Neither the media filename parser nor an explicit `Ep`/`SxxE` token found an episode. |
| `filename_contains_multiple_explicit_episode_numbers` | More than one distinct explicit episode token was present. |
| `filename_parser_missed_explicit_episode_number` | An explicit episode token was found, but the general filename parser returned no ordinary episode. |
| `filename_parser_episode_has_no_explicit_token` | The general parser found an episode, but no explicit token independently corroborated it. |
| `filename_parser_episode_differs_from_explicit_token` | The general parser and the explicit token produced different episode numbers. |
| `declared_anilist_id_not_found_in_metadata` | Exact metadata lookup returned no entry for the manifest's declared AniList ID. |
| `declared_anilist_entry_has_no_titles` | The declared AniList entry exists but provides no titles for identity checking. |
| `filename_title_not_equal_to_declared_anilist_titles` | The normalized parsed filename title exactly matched none of the declared entry's romaji, English, native, or synonym titles. |
| `declared_anilist_entry_has_no_episode_count` | The declared entry has titles but no episode count for the bounds check. |
| `declared_anilist_entry_is_movie` | The declared AniList entry is a movie and cannot accept an ordinary episode binding. |
| `filename_episode_exceeds_declared_anilist_episode_count` | The agreed filename episode is greater than the declared entry's episode count. |
| `filename_episode_and_title_match_declared_anilist_entry` | The parser and explicit token agree, the title exactly matches, and the episode is in bounds; a proposal is emitted. |

Canary examples include the stem, parsed title, parser episode, explicit episode
tokens, declared series ID, and the exact metadata titles/count/format used in
the comparison. Aggregate counts alone do not provide enough context
for interpreting a quarantine.

- No compatible heavy worker: the Dagster step remains queued; existing heads
  remain available.
- Worker dies before marker: retry reuses the fingerprinted staging prefix;
  no domain head advances.
- Marker written but acknowledgement lost: retry verifies and reuses it.
- Server verification fails: Dagster step fails and no product head advances.
- Later step fails: earlier commits remain and the overall run is failed.
- FastAPI stops: Dagster continues; operator decisions are temporarily
  unavailable.
- Dagster UI stops: daemon/workers continue if durable services remain healthy.
- Dagster storage is lost: product heads remain interpretable from DuckLake;
  raw execution history is restored from Dagster PostgreSQL backups.

## Adding a new product or campaign

1. Define the product models, fingerprint, compiler, and idempotent commit under
   `products/<concern>/`.
2. Add bounded current/eligibility queries at the product's natural key.
3. Add a thin Dagster asset that calls the product and emits materialization
   metadata referencing the domain materialization ID.
4. Add presentation metadata only if the workbench should expose the node.
5. Create or revise one `OperatorCampaign`; its actual Dagster selection owns
   execution while its lens declares and validates the conclusion it presents.
6. Test success, retry/reuse, upstream change, partial failure, and the operator
   projection against real tables.
7. For heavy work, add a discriminated payload to `workers/contracts.py` and an
   environment adapter. Do not make the environment import Dagster.

## Further reading

- [Dagster assets](https://docs.dagster.io/guides/build/assets)
- [Dagster jobs and execution](https://docs.dagster.io/guides/build/jobs)
- [Dagster run executors](https://docs.dagster.io/guides/operate/run-executors)
- [Celery task retry guidance](https://docs.celeryq.dev/en/stable/userguide/tasks.html)
- [DuckLake specification](https://ducklake.select/)
- Martin Fowler, [Data Mesh Principles](https://martinfowler.com/articles/data-mesh-principles.html), for product ownership vocabulary (not as a mandate for services)
- Pat Helland, [Life Beyond Distributed Transactions](https://www.cidrdb.org/cidr2007/papers/cidr07p15.pdf), for independently committed workflow steps
