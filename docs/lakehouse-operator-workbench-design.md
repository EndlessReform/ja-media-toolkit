# Lakehouse operator workbench design

**Status:** directional design, first read-only slice implemented 2026-07-18.

The implemented execution, cache, lineage, and contributor contracts are now
authoritative in
[`packages/data/ARCHITECTURE.md`](../packages/data/ARCHITECTURE.md). This
document preserves the broader product vocabulary and later-stage ideas; its
O0–O3 sequence is historical and must not be read as a claim that every
proposed target, fixture campaign, mapped run item, or approval abstraction was
built. The next approved gate is narrower: input-bound operator
decisions, applied first to competing canonical candidates and bindings.

It describes the surface-neutral operator model and its CLI, HTTP, and
interactive adapters. The first implementation remains deliberately small,
but its contracts must not assume that the data layer is one linear episode
pipeline or that Textual owns application logic.

## Recommendation

Build a **campaign-oriented operator workbench** over a compositional product
graph.

The workbench home is a portfolio of saved campaigns such as:

- “build the current Audiobookshelf library”;
- “compare subtitle LID recipes over these five series”;
- “produce timing-eval dataset v3”;
- “align all Japanese subtitle candidates for this season”; or
- “materialize transcripts with two ASR recipes and grade both.”

A campaign binds four things:

1. a desired target product;
2. a scope or saved membership set;
3. named recipe choices for relevant stages; and
4. an optional stop boundary.

The workbench derives progress from durable products, run records, and approval
decisions. It does not store a mutable percentage-complete counter and it does
not become a scheduler. Opening the campaign on another machine reconstructs
where work stopped, what changed, what can run here, and what is blocked.

The episode × stage matrix remains valuable, but it is one **target lens**. It
is not the application data model and it is not tied to a particular UI
technology.

## Concrete problem

The operator needs to move among many kinds of work without carrying hashes,
remembering which recipe was active, or inferring state from folders and shell
history. The system must answer:

- What products am I currently trying to build?
- Which recipe revision and upstream products does each one require?
- What is current, stale, missing, failed, awaiting approval, or impossible on
  this machine?
- If a target branches into several models or subtitle candidates, which
  branches have completed and which branch is selected downstream?
- If several targets share an expensive ancestor, will it be reused?
- Where did the last run spend its time and which mapped item failed?
- Which comparison requires a human decision, and exactly which input revision am
  I approving?
- Can I stop at a useful intermediate product and continue on another machine?

The answer cannot be “run the next stage.” Many legitimate workloads have no
single next stage.

## Repository facts and measured results

The design starts from existing repository behavior, not a hypothetical DAG:

- Phase D has a real proposal → acceptance → canonicalization → subtitle-LID
  closure with fingerprints, materializations, and run records.
- The subtitle path fans out by subtitle stream. Canonicalization is a fan-in
  choice over accepted captures.
- Forced alignment consumes both audio and a text/cue product and emits word
  and group alignments.
- ASR commonly maps over VAD chunks, then merges chunk results.
- Subtitle cleaning, alignment, and grading may use different model servers,
  VRAM budgets, and machines in different sittings.
- Audiobookshelf publication fans out over episodes but also requires
  series-level metadata and a completed publication layout.
- Evaluation datasets aggregate selected products across episodes and series;
  they may branch across recipes before grading or curation chooses a winner.
- FastAPI is already the repository's normal Python HTTP boundary, including
  local reader tools and LAN services. One exploration already proves HTMX
  filtering, pagination, and mutations; lightweight static-JavaScript readers
  also exist.
- Existing Textual applications prove that keyboard-driven tables and detail
  panes are comfortable, but none supplies a reusable operator application
  core. The repository has no established React application or frontend state
  architecture; Vite currently arrives transitively through the Astro docs
  build.

Phase D tests measure the simple closure. Corpus-scale status-query latency,
planner cost estimates, and the usability of the proposed workboard remain
assumptions to validate.

## Workload shapes the model must represent

| Shape | Example | Consequence |
| --- | --- | --- |
| One-to-one map | canonical subtitle → LID result | One product instance can be independently current or stale. |
| Fan-out | episode → subtitle candidates; audio → VAD chunks | A stage cell may contain many product instances, not one status. |
| Fan-in selection | accepted captures → canonical capture | Selection policy and losing candidates must remain inspectable. |
| Multi-input join | audio + cleaned cues → alignment | Readiness depends on several independently versioned products. |
| Map then reduce | chunks → ASR segments → merged transcript | Partial map progress is useful even before reduction can run. |
| Forked recipes | one subtitle through two LID or cleaning models | Recipe outputs must coexist and be comparable. |
| Optional branch | no subtitle, or audio LID intentionally disabled | `not_applicable` is different from missing or blocked. |
| Conditional expansion | detected chunks or fetched candidates create later work | The complete plan may not be knowable before an upstream commit. |
| Episode aggregation | episode bundles → Audiobookshelf series | A series product depends on a variable set of episode products. |
| Cross-series aggregation | approved alignments → eval dataset | The row grain is a cohort/dataset, not necessarily an episode. |
| Human gate | choose canonical candidate or approve timing quality | Downstream work blocks on a fingerprint-bound decision. |
| Capability boundary | Qwen alignment on CUDA; light LID on laptop | A valid plan may be runnable only in parts on the current machine. |
| External service precondition | vLLM model server or metadata API | “Implemented” does not imply locally runnable right now. |
| Maintenance target | publish, export, snapshot, or GC | Operational sinks belong in the same dependency vocabulary. |

These shapes rule out a fixed ordered list of stages as the kernel contract.

## Candidate target families

The registry should be tested against a broader target vocabulary than Phase D
even while most entries remain unimplemented:

### Identity and canonical inputs

```text
accepted-bindings
canonical-episode-inputs
canonical-subtitle-inputs
```

These are light compilers and decision boundaries. They establish the episode
and candidate subjects reused everywhere else.

### Subtitle analysis and selection

```text
subtitle-lid
subtitle-lid-comparison
cleaned-subtitles
aligned-subtitle-candidates
selected-japanese-subtitle
```

This family exercises candidate fan-out, multiple recipes, audio+text joins,
quality metrics, and a human or policy selection fan-in.

### Audio analysis and transcripts

```text
audio-lid
vad-segments
asr-transcript
asr-comparison
diarized-transcript
healed-transcript
```

These targets add dynamic chunk/speaker expansion, hardware-specific recipes,
map/reduce assembly, and optional metadata-healing branches.

### Learner-facing publication

```text
portable-episode-audio
episode-bundle
audiobookshelf-series
audiobookshelf-library
transcript-search-index
```

An Audiobookshelf library target composes canonical identity, selected audio and
subtitles, transcode/layout recipes, episode-level bundles, series metadata,
and a library-level projection. It is a useful proof that “target” means a sink
with shared ancestry—not one extra pipeline stage.

### Evaluation and dataset products

```text
timing-grade-report
alignment-comparison
timing-eval-dataset
asr-eval-dataset
training-candidate-set
webdataset-export
```

These aggregate across episodes/series, pin exact recipe outputs, pass through
quality/approval gates, and produce immutable dataset snapshots. A dataset
target must never scrape “whatever is current” after publication; membership
and product fingerprints are part of its durable manifest.

### Operational products

```text
catalog-snapshot
publication-diff
gc-candidate-report
consistency-report
```

Read-only reports fit the target model. Destructive GC remains a separately
confirmed operation whose dry-run report is the input to any later mutation.

This list is illustrative, not a promise to implement empty registry entries
for every noun. O0 should register only enough typed stubs/fixtures to prove the
different shapes and keep real unimplemented entries explicit.

## Domain model

### Products, stages, recipes, and targets

Keep four concepts distinct:

- A **product** is a durable typed result: a canonical episode input, one
  subtitle-LID analysis, one aligned cue set, an episode bundle, or a pinned
  eval dataset.
- A **stage** is code that can produce product instances from other product
  instances.
- A **recipe** is a named, validated parameterization of one stage, including
  its content hash and capability requirements.
- A **target** is an operator-facing sink or pause point. It asks for a product
  family and lets the planner walk dependencies backward.

Targets are not routes through a pipeline. `audiobookshelf-library`,
`subtitle-lid-comparison`, `timing-eval-dataset`, and `aligned-subtitles` may
reuse ancestors while choosing different recipes or downstream branches.

### Product identity

The planner needs a stable logical identity finer than `(target, scope)`:

```text
ProductKey
  product_type       canonical_subtitle, subtitle_lid, aligned_cues, ...
  subject            episode, subtitle stream, audio chunk, series, dataset
  variant            candidate/model/output role when the product family branches
  recipe_revision    named recipe plus content hash
```

Examples:

```text
subtitle_lid / anilist:15451:e003:stream-4 / fasttext-default@sha256:...
aligned_cues / anilist:15451:e003:stream-4 / qwen3-vllm@sha256:...
abs_episode / anilist:15451:e003 / portable-aac-v1@sha256:...
eval_dataset / timing-regression-v3 / approved-qwen-alignments@sha256:...
```

Do not introduce a generic EAV artifact ledger merely to store these keys.
Domain result tables remain authoritative. Each registered stage supplies a
bulk status adapter that projects its domain rows into product observations.
The generic model exists in Python for planning and presentation.

### Stage specification

A static registry describes executable structure in code:

```text
StageSpec
  name
  input product families
  output product families
  expansion function
  fingerprint function
  status adapter
  runner
  commit granularity
  capability requirements
  approval requirements
```

The expansion function matters more than a list of dependencies. Given demanded
outputs, it returns concrete work items and their input demands. That function
can express mapping, joining, selection, and aggregation without teaching
individual surface adapters domain rules.

The registry must be the single source for CLI/API target introspection,
planner behavior, and target-lens construction. A route, template, or widget
never maintains its own stage list.

### Recipe registry

Recipes are paged and searchable first-class objects, not strings buried in
result rows. A recipe record exposes:

- stable family and name;
- content hash/revision;
- stage and product types;
- concise parameter summary and full source path;
- capability requirements such as CPU, Apple Silicon, CUDA, minimum VRAM, or
  a named external service;
- lifecycle tags such as default, experimental, candidate, superseded, or
  pinned;
- compatible upstream/output schema versions; and
- observed run counts, failures, durations, and product currency for the
  active campaign scope.

The registry is assembled from checked-in recipe files plus observed durable
products and runs. Historical recipe results must coexist long enough to compare
them. Recipe-bearing result tables therefore cannot permanently use “replace
the one current recipe” semantics; they need merge/append-by-product-key with
explicit GC or a current-selection view.

### Campaigns

A campaign is saved operator intent, not queued work:

```text
Campaign
  campaign_id
  label and note
  target
  scope selector or named membership set
  recipe bindings
  optional stop target
  created/updated timestamps
```

Examples:

```text
spring-2026-abs
  target = audiobookshelf-library
  scope = named-set:spring-2026-anime
  recipes = portable-aac-v1, japanese-sub-selection-v2, abs-layout-v1

lid-bakeoff-15451
  target = subtitle-lid-comparison
  scope = series:anilist:15451
  recipes = script-fasttext-v1, cld3-candidate-v1

timing-eval-v3
  target = timing-eval-dataset
  scope = named-set:timing-regression-corpus
  stop = approval:timing-quality
```

Progress is recomputed from products and gates. Changing a recipe binding
immediately changes desired fingerprints and exposes stale downstream work; it
does not mutate historical outputs.

This preset proposal is superseded for executable campaigns. A supported
campaign is now an actual Dagster job structurally coupled to an operator lens;
deployment TOML does not duplicate job identity or asset selection. Future
saved scopes may live in ordinary PostgreSQL, but they must parameterize a
registered campaign rather than define another graph. Local cursor/filter state
belongs only in `~/.local/state/ja-media-toolkit/`.

## Planning model

### Backward demand planning

Planning starts with an execution intent:

```text
target + scope + recipe bindings + optional stop boundary
```

The planner walks backward from demanded product keys:

1. Ask the product's registered producer to expand the demand.
2. Compare the desired fingerprint with observed products.
3. Reuse current products.
4. Add missing or stale work items.
5. Recursively demand their inputs.
6. Deduplicate shared ancestors by product key.
7. Stop at current products, explicit stop boundaries, approval gates, or
   capability barriers.
8. Topologically group currently knowable work into execution waves.

The output is an immutable `ExecutionPlan`; running it is a separate operation.
CLI, HTTP, and interactive adapters render the same plan DTO.

### Dynamic expansion and re-planning

Some downstream product keys do not exist until an upstream stage runs. VAD
discovers chunks; subtitle retrieval discovers candidates; diarization discovers
speakers. The planner must represent a **deferred frontier**:

```text
wave 1: fetch subtitles for 12 episodes
frontier: candidate LID work expands after fetch commits
estimated downstream cardinality: unknown
```

Execution proceeds in waves:

1. plan the knowable closure;
2. run one eligible wave;
3. refresh durable state;
4. re-plan from the original intent; and
5. continue until the sink, stop boundary, approval gate, or capability barrier.

This is ordinary deterministic control flow, not a durable scheduler. If the
process stops, opening the campaign and planning again reconstructs the next
wave from committed products.

### Alternative producers and recipe composition

When several stages can produce the same product family, the planner never
chooses silently. A target preset or campaign binds a named strategy/recipe.
Missing bindings appear as a planning question, not an arbitrary default.

Recipe composition should be explicit but economical. A target may specify
only downstream choices and inherit registered defaults upstream; the plan
preview expands the complete effective recipe set before confirmation.

### Commit granularity and partial progress

The Phase D proof replaces corpus tables atomically. That remains valid for
small corpus-wide compilers, but it is not sufficient for long mapped jobs.
Each stage declares a commit granularity:

- corpus;
- named set;
- series;
- episode;
- product instance; or
- bounded batch of product instances.

Mapped outputs may commit independently. A GPU batch run can therefore finish
37 subtitle alignments, fail on item 38, and preserve those 37 current products.
A downstream reducer remains blocked until its required set is complete. The
workbench derives both mapped progress and reducer readiness.

## Truthful status model

Do not force every condition into one enum. A product/work item has orthogonal
facets:

```text
implementation: implemented | not_implemented
applicability:   applicable | not_applicable | unknown
currency:        missing | current | stale | unknown
readiness:       ready | blocked_inputs | blocked_approval | blocked_capability
execution:       idle | started | succeeded | failed
```

This avoids lies such as rendering an intentionally absent audio-LID stage as
missing, or treating a CUDA-only aligner as failed on the laptop.

The shared store cannot truthfully prove that a non-terminal remote process is
still alive without heartbeats. Therefore:

- a durable run row with no terminal state is `started` or `unconfirmed`;
- the launching adapter may additionally show `running_here` while its local
  subprocess exists;
- elapsed time alone never changes a remote run to `abandoned`; and
- a later explicit “mark abandoned” action, if added, is a human decision.

### Aggregate cells

A matrix cell often summarizes several product instances. It should render a
counted aggregate such as:

```text
LID  3/4 current · 1 stale
Align 2 current · 2 approval
ASR  18/24 chunks · running here
```

Enter expands the cell into its product instances, recipes, fingerprints, and
blocking reasons. The UI never collapses “three current and one failed” into a
single green or red icon.

## Run and timing model

The implemented model has two execution levels:

```text
Run
  one Dagster job execution, monotonic Run #, intent, terminal state

Dagster step
  one computation execution, timing, disposition, and failure
```

Domain products commit independently of the enclosing run; a later failure
does not erase an earlier successful product. Compacted worker handoff rows
provide per-item progress only for distributed stages that need it.

Standard timings should include total wall time plus optional named phases:

- input discovery/download;
- decoding/preprocessing;
- model load or service wait;
- inference/compute;
- postprocessing/validation; and
- commit/upload.

Stages may add domain metrics such as audio duration, real-time factor, tokens,
cue count, batch size, peak VRAM, or bytes written. Keep stage-specific metrics
as validated structured JSON rather than adding a column per model.

The workbench can derive historical median and p90 duration by stage, recipe,
capability class, and coarse size bucket. Plan estimates must be labeled as
estimates and show when history is insufficient.

### Failure drill-down

Selecting a failed aggregate walks this path:

```text
campaign → target/stage → run → run item → failure details
```

The detail view shows:

- concise exception and stage-provided diagnosis;
- machine, capability profile, recipe revision, and exact input products;
- timing breakdown and the last successful revision for comparison;
- log/artifact URI and a bounded log tail;
- whether retrying would reuse or recompute ancestors;
- downstream products currently blocked by the failure; and
- the exact retry plan, still subject to normal confirmation.

Specialized media inspection should open or hand off to existing focused tools
such as subsync and subtitle-cleaning review. The workbench should not absorb a
waveform editor, subtitle diff viewer, and every future analyst interface into
one application.

The same boundary applies to the separately proposed evaluation workbench. If
Metaflow or another eval-specific runner remains useful there, it publishes
typed artifact references and bounded run/status projections through an
adapter. The lakehouse workbench must not query an orchestrator's internal
database as a domain API or force ordinary data-layer stages onto that runner.

## Standardized human approval gates

There are two different confirmations:

1. **Dispatch confirmation** is ephemeral: “run these 143 items here, estimated
   48 minutes.” It prevents accidental work but is not a domain artifact.
2. **Domain approval** is durable: “this alignment/canonical choice/dataset is
   acceptable for downstream publication.” It must survive sessions
   and machines.

Model domain approval as a standard gate with stage-specific comparison inputs:

```text
ApprovalRequest
  gate type and semantic subject
  exact input fingerprint
  requesting stage/recipe/run
  question, risk/severity, and concise rationale
  comparison inputs and artifact references
  allowed decisions
  downstream impact summary

ApprovalDecision
  request/gate key and exact input fingerprint
  approve | reject | waive
  actor, note, and timestamp
  optional supersession/retirement
```

Machine-produced requests belong with derived lake products. Small concurrent
human decisions belong in ordinary PostgreSQL, like binding overrides. A
decision matches only the exact input fingerprint. If an upstream product or
recipe changes, the old decision remains historical and the new request is
pending; approval never leaks across changed inputs.

Interactive surfaces render every request through one semantic `ApprovalCard`
view model:

- what am I deciding;
- why did the pipeline escalate;
- which inputs and comparisons can I inspect;
- what does each decision do;
- how many downstream products are blocked; and
- which exact revision the decision covers.

Gate-specific renderers may enrich the comparison pane, but they do not invent
new decision persistence paths. Binding correction, subtitle selection, timing
quality, publication review, and dataset inclusion can share this protocol.

## Surface-neutral application architecture

All domain and application logic lives behind an in-process
`OperatorApplication` interface:

```text
OperatorApplication
  list_campaigns(query, page)
  get_campaign_snapshot(campaign_id, lens, page)
  list_recipes(query, campaign_id, page)
  get_recipe(recipe_id, campaign_id)
  plan(execution_intent)
  list_runs(query, page)
  get_run(run_id)
  get_run_item(run_id, item_id)
  list_approvals(query, page)
  get_approval(request_id)
  decide_approval(command)          later mutation phase
  dispatch(plan_token)              later mutation phase
```

Inputs and outputs are typed, JSON-serializable DTOs. The application service
owns validation, pagination, snapshot composition, planning, authorization of
domain transitions, and stale-plan rejection. It calls repositories and stage
adapters; it never imports FastAPI, HTMX, React, or Textual.

Adapters are deliberately thin:

```text
OperatorApplication
  ├── CLI: renders tables or JSON
  ├── FastAPI JSON routes: HTTP validation/status codes
  ├── FastAPI HTML routes: templates and HTMX fragments
  └── optional Textual client: keyboard/layout rendering
```

The HTML routes may call the application service directly in process rather
than making loopback HTTP requests. They still render the same response DTOs
returned by JSON routes. Contract tests ensure CLI, JSON, and HTML do not grow
different definitions of current, stale, blocked, or approved.

### FastAPI boundary

The first HTTP adapter is a locally launched process:

```text
ja-data web --port 8766    # always binds 127.0.0.1
```

It is not added to the shared Compose deployment, Caddy, or monitoring stack in
the first phase. Loopback binding lets it reuse the operator's configured
DuckLake/PostgreSQL/Garage access without creating LAN authentication and
authorization work accidentally.

Use separate route families:

```text
/api/operator/v1/...    typed JSON application API
/operator/...           HTML pages and HTMX fragments
```

API routes return the application DTOs, not DuckLake rows or arbitrary SQL
results. HTML fragment routes own markup only. Mutating API endpoints do not
exist until their domain command services and authorization boundary are
implemented.

If remote browser access becomes valuable, exposing this process beyond
loopback is a separate service/security decision. It requires authentication,
CSRF policy, deployment ownership, health/metrics, and the repository's
`add-service` workflow; “FastAPI already works locally” is not permission to
skip those costs.

## Surface-neutral information architecture

### Global context bar

Every interactive surface shows the active campaign/scope, target lens,
effective recipe preset, local machine capability summary, catalog refresh
time, and whether a local run subprocess is attached.

Changing campaign or recipe context updates projections; it never launches
work implicitly.

### 1. Workboard (home)

One row per saved or recently opened campaign:

```text
Campaign              Target                    Progress       Blockers      Last activity
spring-2026-abs       audiobookshelf-library    112/144        3 failures    18m ago
lid-bakeoff-15451     subtitle-lid-comparison   71/96×2        25 stale      yesterday
timing-eval-v3        timing-eval-dataset       438/500        12 approval   4d ago
```

Filters include active/pinned, target family, stale, failed, awaiting approval,
and runnable-here. Opening a campaign enters its target lens; a plan action
previews the recomputed closure.

### 2. Target lens

The target lens chooses a useful row grain and columns from the target closure:

- episode rows for Audiobookshelf or subtitle work;
- subtitle-candidate rows for comparison/alignment;
- series rows for publication;
- cohort/shard rows for dataset builds; or
- chunk rows for ASR diagnostics.

For an episode-oriented target it may look like:

```text
Episode  Canonical  Subs  LID             Align             ABS bundle  Issues
e001     current    2     2 current       1 selected        current
e002     override   3     2/3 · 1 stale   2 approval        blocked     2
e003     missing    —     not applicable  blocked input     blocked     1
```

Columns are not globally fixed. A narrow **lane preset** shows the selected
target's dependency closure, while the operator may pin a few product families
for comparison. This prevents a 30-stage pipeline from becoming a 30-column
unreadable table.

### 3. Recipe registry

Paged/searchable recipe rows show:

```text
Recipe                    Stage          Scope progress   Failure  p50/p90    Tags
script-fasttext-v1        subtitle-lid   71/96 current    0        14/22 ms   default
cld3-candidate-v1         subtitle-lid   18/96 current    2        9/17 ms    candidate
qwen3-vllm-align-v2       align          40/71 current    3        31/48 s    cuda,pinned
portable-aac-v1           transcode      112/144 current  0        8/14 s     default
```

Progress is always relative to the active campaign/scope. Enter shows recipe
parameters, dependencies, revisions, materialized products, run history,
comparisons, and downstream targets that use it. Operators can switch the
campaign's recipe binding in a later mutation phase, after previewing the
resulting stale set.

### 4. Plan preview

The preview is the guardrail between visibility and execution:

```text
Intent: timing-eval-v3 → timing-eval-dataset

Reuse       438 products
Wave 1       37 clean-subtitle items       runnable here      ~4m
Wave 2       62 qwen-align items           CUDA ≥ 16 GB       ~39m
Deferred      ? grading items              expands after align
Approval     timing-quality                stop boundary

Changed recipe bindings: qwen-align v1 → v2
Expected invalidation: 62 alignments, downstream grades, dataset snapshot
```

The operator can inspect deduplicated ancestry, expanded work items, blockers,
capability placement, recipe revisions, estimates, and deferred frontiers. The
plan may be exported as JSON for diagnostics, but the normal remote handoff is
an exact command using a named campaign/set—not copied fingerprints.

### 5. Runs

Shows invocations across campaigns and machines, with filters for active,
failed, partial, recipe, machine, and time. Enter drills into mapped run items
and timings. A local launched subprocess may expose live progress/log tail;
remote work updates only as its durable records change.

### 6. Approval inbox

Shows pending/stale decisions ordered by severity and downstream impact.
Entering a request opens its standardized approval card and any specialized
comparison viewer. Bulk approval is disabled initially; it requires a separately
designed gate policy because “same type” does not imply “same inputs.”

### 7. Findings and data quality

Cross-store consistency findings, malformed inputs, and domain quarantine
issues remain visible even when they do not block the active target. This is a
diagnostic inbox, distinct from approval requests.

## Frontend stack recommendation

### Start with FastAPI + server-rendered HTML + HTMX

The first read-only workbench primarily needs paged tables, filters, target-lens
switching, detail drawers/pages, plan previews, polling, and approval forms.
These are a good fit for server-rendered HTML with HTMX fragments:

- the application state already lives on the server/shared stores;
- the expected corpus is modest enough for server-side paging and filtering;
- local/LAN round-trip latency is small;
- Python route/template tests fit the existing toolchain;
- there is no Node application architecture to establish yet; and
- HTMX keeps the first surface close to the DTOs while the interaction model is
  still being discovered.

Use real templates and small static modules. Do not repeat the exploratory
pattern of constructing large HTML strings in Python. Vendor a reviewed HTMX
asset or pin it through the package's static assets; the operator surface must
not require a public CDN at runtime.

HTMX routes should use URLs and forms that work as ordinary server-rendered
navigation first. HTMX progressively replaces workboard, table, detail, and
plan regions. Browser history and shareable local URLs remain meaningful.

Polling is sufficient for shared-state refresh. A locally launched run may use
Server-Sent Events for structured progress/log tail if polling proves clumsy;
do not begin with WebSockets.

### Keep the JSON API SPA-ready

HTMX is the first adapter, not an architectural commitment. JSON response
contracts, cursor pagination, filter objects, error envelopes, and command DTOs
must be complete enough that a future Vite client does not need HTML scraping
or new domain queries.

A React SPA can later mount at `/operator/app` or replace HTML routes while the
FastAPI API and operator core remain unchanged. Generated TypeScript types from
the OpenAPI schema are preferable to manually duplicating DTOs if that phase
arrives.

### Conditions that would justify Vite + React

Adopt a SPA when measured use requires several of these, not merely because the
domain has many entities:

- large virtualized grids that cannot be paged without harming the workflow;
- correlated selection/state across several simultaneously visible panes;
- rich recipe comparison charts with brushing and linked filters;
- interactive lineage/plan manipulation rather than read-only ancestry;
- embedded waveform, subtitle-timeline, or artifact comparison work that
  cannot remain in focused tools;
- sustained live updates from many concurrent run items;
- optimistic bulk mutations with complex undo/selection state; or
- offline/client-side exploration of a downloaded snapshot.

The cost is real: a second package/toolchain, client routing and state policy,
OpenAPI client generation, frontend unit/component/browser tests, static asset
packaging, and duplicated error/loading concerns. Those costs are justified for
a genuinely rich analytical client, not for paginated operational tables.

### Textual's remaining role

Textual remains a plausible compact client for compute machines or a
keyboard-first status view. If built, it imports only application DTOs and
methods. It never becomes the home of planning, status composition, approvals,
or execution semantics. There is no requirement to build it before or alongside
the web workbench.

## Interaction and dispatch boundaries

The first workbench slice is read-only. Refresh, filters, campaign switching,
recipe paging, plan preview, lineage inspection, failure drill-down, and run
timings all operate through UI-independent query/planner services.

Later local dispatch follows these rules:

- An interactive adapter launches the same `ja-data run` operation the CLI
  exposes, preferably as a subprocess with structured JSONL progress rather
  than model work inside an HTTP request or UI event loop.
- The planner runs again immediately before dispatch; confirmation is invalid
  if desired fingerprints changed since preview.
- No UI adapter SSHes, starts a remote service, or acts as a work queue.
- If a wave is runnable elsewhere, show why and generate an exact command using
  the campaign or named set.
- A local run can stop cleanly after the current commit unit. Hard cancellation
  is not promised until runners define safe cancellation points.
- Mutation actions—recipe binding, campaign editing, approvals, binding
  override, and abandonment marking—arrive only after their independent domain
  services and persistence contracts are tested.

## Snapshot/query architecture

Every adapter receives immutable paged snapshot DTOs from the operator
application. Route handlers, templates, browser scripts, and widgets never
query DuckLake or PostgreSQL directly.

```text
OperatorSnapshot
  campaigns and active context
  target/recipe registry metadata
  aggregate product observations
  run and run-item summaries
  approval summaries
  findings
  snapshot timestamps/source versions
```

Stage status adapters must bulk-load their domain tables. The current
row-at-a-time override composition is unsuitable for a matrix; the projection
loads the small active override set once and composes it in memory or through a
bounded relation. No widget-triggered N+1 point reads.

DuckLake facts and PostgreSQL decisions cannot be read in one cross-system
transaction. The snapshot records both read timestamps and detects if an
override head changes during composition; if it cannot obtain a coherent
bounded read, it marks the affected projection refreshing instead of presenting
false certainty.

Connections are opened per request/refresh operation and not shared across
worker threads. Expensive snapshot compilation may use a bounded application
cache keyed by source versions, never a UI-owned cache. Refresh results replace
the prior page model atomically; interactive adapters keep the previous
snapshot visible with a refreshing indicator.

## Package and module shape

The operator application and local web adapter remain in `packages/data` under
`ja-data`; putting them in `packages/frontend` would pull
DuckLake/PostgreSQL plant operation into the learner-facing `ja-media`
environment. Add FastAPI, Uvicorn, Jinja2, and HTML-test dependencies through
`uv add` when the HTTP phase begins. Do not add React/Vite dependencies unless
the SPA gate is met.

Keep modules responsibility-sized:

```text
packages/data/src/ja_media_data/operator/
  products.py          ProductKey, observations, status facets
  registry.py          StageSpec, RecipeSpec, TargetSpec registries
  planning.py          backward expansion, deduplication, waves/frontiers
  campaigns.py         campaign/scope loading and validation
  snapshot.py          bulk UI-independent operator projection
  approvals.py         request/decision composition contracts
  runs.py              run-item/timing projections
  application.py       surface-neutral use cases and commands
  api_models.py        JSON-serializable request/response DTOs
  presentation.py      aggregate cells and target-lens view models
  http/
    app.py             FastAPI construction and lifespan/resources
    api_routes.py      /api/operator/v1 JSON adapter
    html_routes.py     /operator page/fragment adapter
    templates/         complete pages and HTMX fragments
    static/            vendored HTMX plus small CSS/JS modules
  tui/                  optional later thin adapter
```

No module should approach the repository's 500-line hard limit. Reusable
templates/components/widgets stay presentation-only; query, planning, and
mutation logic remain testable without FastAPI, HTMX, React, or Textual.

## Incremental implementation plan

### O0 — prove the headless compositional application core

1. Define `ProductKey`, status facets, `WorkItem`, `ExecutionPlan`, deferred
   frontier, capability requirement, `RecipeSpec`, `TargetSpec`, and campaign
   DTOs.
2. Replace the Phase D hard-coded target tuple with the registry shape while
   preserving its CLI behavior.
3. Build synthetic fixture graphs proving:
   - shared ancestry/fork deduplication;
   - subtitle fan-out;
   - audio+text join;
   - latest/selected fan-in;
   - map/reduce readiness;
   - two concurrent recipe branches;
   - dynamic expansion after an upstream commit;
   - approval and capability barriers; and
   - an aggregate dataset target across series.
4. Add a bulk Phase D status adapter and recipe observations.
5. Implement `OperatorApplication` queries for campaigns, target lenses,
   recipes, plans, runs, and details over real Phase D tables.
6. Produce stable JSON-serializable responses without importing any UI or HTTP
   framework.

**Gate:** the model can express an Audiobookshelf target, an LID bakeoff, and an
eval-dataset target in fixtures without planner special cases. CLI JSON can
render the real Phase D campaign, recipe page, target lens, and plan.

### O1 — FastAPI JSON adapter

1. Add FastAPI/Uvicorn to `packages/data` through `uv add` and expose
   `ja-data web` bound to loopback by default.
2. Implement read-only `/api/operator/v1` routes over application DTOs.
3. Define cursor pagination, filters, error envelopes, request correlation, and
   snapshot/source-version metadata.
4. Add OpenAPI/schema and FastAPI dependency-override tests so repositories can
   be replaced by fixtures.
5. Keep the adapter out of Compose/Caddy and add no mutations.

**Gate:** every operator query available through CLI JSON is available through
typed HTTP without route-specific domain queries or framework types leaking
into the application core.

### O2 — first read-only FastAPI/HTMX workbench

1. Add proper templates and vendored/pinned HTMX/static assets.
2. Implement workboard, target lens, recipe registry, plan preview, runs,
   approval placeholders, and details over the application DTOs.
3. Ship one built-in Phase D campaign so the real proposal → acceptance →
   canonical → subtitle-LID path is inspectable immediately.
4. Show future targets/recipes as `not_implemented`, not missing work.
5. Add server-side filtering/paging, stable URLs, browser-history behavior,
   polling refresh, and responsive narrow-window layouts.
6. Keep ordinary link/form navigation functional without HTMX where practical.

**Gate:** from the browser, the operator can explain why a canonical capture
won, inspect subtitle-LID products and timings, page recipes, preview a closure,
and drill from a failed aggregate to its failure details. The workbench performs no
writes and requires no public CDN or Node build.

### O2.5 — measured frontend decision

Use the real O2 workbench for the Phase D campaign and fixture campaigns. Record
specific friction in recipe comparison, target-lens navigation, plan preview,
failure drill-down, refresh, and browser state.

- Continue with HTMX if the work remains page/table/detail oriented.
- Introduce a Vite/React package only if the measured SPA criteria above are
  met and a small React spike materially improves the worst interaction.
- A React decision changes only the interactive adapter plan; O0/O1 contracts
  remain fixed unless they are independently deficient.

**Gate:** document the observed interaction that requires or rejects a SPA; do
not choose based on generic “application complexity.”

### O3 — mapped run records and truthful progress (superseded)

1. Dagster owns run and step history. The old `pipeline_runs` and
   `run_stage_checkpoints` migrations are inert historical tables with no
   application reader or writer.
2. Add standard timing metrics and structured JSONL progress for local runs.
3. Adapt Phase D stages to explicit commit granularity and run-item reporting.
4. Add history-derived duration estimates.

**Gate:** a partial mapped fixture run preserves successes, exposes one failed
item and its phase timings, and replans only incomplete products.

### O4 — local dispatch

1. Launch confirmed locally runnable waves as subprocesses.
2. Revalidate plans before launch and attach local structured progress/log
   tail through polling or SSE.
3. Support stop-after-wave and copyable elsewhere commands for capability
   barriers.
4. Never introduce remote execution or an autonomous queue.

**Gate:** a laptop can run light LID work, stop at CUDA alignment, and later
observe workstation results without copying product hashes.

### O5 — standardized approval gates

1. Add derived approval requests and fingerprint-bound PostgreSQL decisions
   through reviewed schemas.
2. Implement the approval inbox/card and one low-risk gate end to end.
3. Good first candidates are canonical-choice escalation or eval-dataset
   inclusion; avoid publication/destructive actions in the first mutation.
4. Add specialized comparison handoff to existing subtitle tools.

**Gate:** changing an upstream fingerprint invalidates effective approval and
blocks downstream work until the changed inputs are reviewed.

### O6 — campaign and recipe mutation

1. Persist ad hoc campaigns only after the checked-in preset shape is proven.
2. Preview invalidation before changing recipe bindings.
3. Add pin/supersede annotations and comparison views without deleting older
   products.
4. Add conservative recipe-result GC as a separate dry-run-first operation.

**Gate:** the operator can leave one campaign, explore another recipe, return,
and see progress reconstructed from durable state rather than UI session data.

## Test strategy

Use four layers:

1. Pure planner and status tests over synthetic graphs. These carry most of the
   combinatorial burden and run without FastAPI or external services.
2. Query-adapter integration tests over disposable PostgreSQL/DuckLake fixtures,
   including cross-store override and approval composition.
3. `OperatorApplication` contract tests reused by CLI and HTTP adapters, plus
   FastAPI `TestClient` tests for JSON status codes, pagination, filters,
   validation, and dependency overrides.
4. HTML/HTMX route tests for complete pages and fragments, with a small browser
   suite for history, polling replacement, focus/selection stability, and
   responsive layouts. If a SPA is adopted, add its unit/component/browser
   toolchain rather than relying on Python tests to validate client behavior.

Do not make ordinary UI/API tests require Garage, model downloads, audio
devices, CUDA, or a live vLLM server. One opt-in live-data smoke can verify that
the snapshot projection remains bounded on the configured corpus.

## Alternatives considered

### Fixed episode × stage dashboard

Fastest first screenshot, but it hard-codes episode grain, hides recipe
branches, and cannot naturally represent datasets, chunks, or series-level
publication. Keep the matrix as a target lens, not the model.

### Generic node-graph canvas

Visually impressive but poor for answering “what is stale across 144 episodes”
and likely to reproduce Dagster's asset-centric mismatch. Use compact ancestry
trees in plan/details and tables for operational scanning.

### One saved linear workflow per use case

Simple until Audiobookshelf, LID, alignment, eval, and ASR each duplicate shared
steps and invent incompatible resume rules. Product-key demand planning gives
reuse and composition without an always-on orchestrator.

### Textual as the primary architecture

Textual would provide a quick keyboard-first client, but recipe comparisons,
timing visualization, comparison links, and browser navigation may outgrow a
terminal. More importantly, putting the application model in widgets would
make a later web surface expensive. Keep Textual optional and thin.

### Start immediately with Vite + React

Provides the richest client-state and visualization ceiling, but the repository
has no established React stack and the first surface is dominated by paged
tables/details. FastAPI DTOs plus HTMX test the interaction model with less
tooling. The SPA remains a measured upgrade path rather than a rewrite.

### HTML-only FastAPI routes with no JSON contract

Would make HTMX fast initially but couple application queries to fragments and
leave a future SPA, CLI, or Textual adapter scraping/reimplementing behavior.
Build the surface-neutral DTOs and JSON adapter first.

### Build mutation and dispatch into the first interactive surface

Would make it difficult to distinguish a status-model error from an execution
error. Prove registry, planner, snapshot, and read-only navigation first; add
mutations only through tested domain services.

### Use an external orchestrator UI

Reintroduces the scheduler/service and asset-node worldview already retired.
The needed surface is campaign-, recipe-, product-, and decision-oriented.

## Decisions made by this plan

- The workbench is campaign-oriented and compositional.
- Targets name sinks/pause points; recipes parameterize stages; products carry
  durable identity.
- Planning is backward from demand, deduplicates shared ancestors, and replans
  in waves across dynamic frontiers.
- The episode matrix is a lens, not the kernel model.
- Recipe browsing, run-item failures/timings, and approval gates are first-class
  navigation areas.
- Durable approvals bind exact input fingerprints and live in PostgreSQL;
  derived requests live with lake products.
- Application logic lives in a headless `OperatorApplication`; CLI, FastAPI,
  HTML/HTMX, and optional Textual/SPA clients are adapters.
- FastAPI provides a typed JSON boundary before an interactive UI.
- FastAPI/HTMX is the first read-only workbench; Vite/React requires measured
  usage results from that workbench.
- Interactive adapters are read-only first and never become remote executors
  or schedulers.
- Domain tables remain authoritative; no generic artifact EAV ledger is added.

## Questions deliberately deferred to measured implementation

1. Whether ad hoc campaigns need shared PostgreSQL persistence or checked-in
   presets plus local recent-history state are sufficient initially.
2. The minimum run-item schema that supports partial mapped progress without
   drifting into orchestration metadata sprawl.
3. Which first approval gate provides the best real test case for the generic
   request/decision interface.
4. Whether recipe duration estimates are useful at current corpus scale and
   granular enough without per-model bespoke code.
5. Whether O2 demonstrates a concrete need for client-side virtualization,
   linked visualization state, or other SPA criteria.
6. Whether the local FastAPI process should ever become a remotely accessible
   first-party service; this is explicitly outside the first workbench phases.
