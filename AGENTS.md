# ja-media-toolkit

This repo is a collection of tools to assist me (a native English speaker, JLPT ~low N3) with managing native content.

## Project goals

The toolkit provides a flexible ecosystem of **Tools** and **Services** to assist a language learner in managing and mining native Japanese media.

### Tools
Utilities for processing and managing local media files (e.g., anime, podcasts, manga CBZ). Current and future focus areas include:
- **ASR & Transcription**: E.g. enerating high-quality transcripts, benchmarking proprietary ASR systems, and implementing automatic "healing" or biasing of transcripts based on known metadata.
- **Media Management**: E.g. splitting audio based on voice activity (VAD), and aligning community subtitles to actual audio.
- **Mining & Analytics**: Diarizing audio for speaker separation and visualizing content for shadowing or sentence mining.

### Services
Infrastructure and APIs that facilitate the tools and coordinate data:
- **Data Mirrors**: Local mirrors of heavyweight datasets (e.g., Kitsunekko) to reduce dependency on upstream git repos.
- **Metadata Bridges**: Crosswalk services to resolve IDs across various anime databases (TVDB, MAL, AniList, etc.).
- **Static Surfaces**: Documentation and search interfaces for transcript corpuses.

*Note: These examples are illustrative; the system is designed to evolve as new language learning workflows are identified.*

## Philosophy

1. **Avoid enterprise brainrot.** Remember that these tools are primarily for my use case and my system: anything useful to the community will be broken out into its own project. So avoid premature abstraction: e.g., don't bother making anything work for Windows, since I don't use it, or for AMD, since I don't care. Focus only on the abstractions that cover the volatility I might actually see; e.g. below:
2. **Data and contracts are permanent, models are ephemeral.** Models will change, runtimes will change, the location of compute will change (laptop vs workstation vs serverless GPU vs GPU server). Decouple _what_ should be done from _who_ is doing it.
3. **Use liberal documentation.** I am using this repo (in part) to learn best practices for system design and audio management: so use a literate style.
    - Ensure all core abstractions have descriptive docstrings. (no need to comment every line though).
    - Explain _why_ key decisions were made
    - Ensure config has nontrivial examples

## Data lake

The repository is evolving from a collection of direct input-to-application
tools and narrow services into a proper data layer built around **compiled data
products** and a pragmatic **medallion architecture**. This is a gradual
migration, not a requirement to rewrite every existing component at once.

Use these layers consistently:

- **Bronze** is immutable or append-oriented captured evidence: source media,
  extracted streams, provider files, source manifests, and provenance. Bronze
  may contain strong hints such as an AniList ID without claiming that inferred
  episode identity, language, timing, or quality is correct.
- **Silver** is normalized, enriched, validated, or joined data compiled from
  bronze: normalized inventories, measured LID, episode hints and bindings,
  aligned subtitles, portable audio, and selected audio/subtitle pairs. Silver
  results must retain exact input and recipe/model versions.
- **Gold** is a consumer-ready projection with an explicit policy: episode
  bundles, Audiobookshelf inputs, application indexes, pinned datasets, and
  publication layouts. Gold is the normal boundary consumed by human-facing
  applications and services.

The default direction is therefore:

```text
bronze evidence -> silver compilation/enrichment -> gold projection -> application
```

Audiobookshelf and similar consumers should eventually consume gold products,
not independently repeat `input -> application output` transformations. Avoid
pushing durable work such as LID, episode resolution, alignment, or selection
policy into application request paths merely because that is where the first
caller appeared. Put reusable compilation in the data layer and let consumers
read stable gold contracts.

### Data products, execution, and storage

- The data layer owns a small explicit execution kernel: target dependencies,
  input fingerprints, materialization history, retries through idempotent
  commits, and operator-visible status. Manual dispatch is intentional; do not
  introduce an orchestrator, scheduler, sensor, or work queue without a new
  measured requirement and architectural review.
- Durable tables, manifests, and media artifacts belong in the data lake in
  open, inspectable forms. DuckLake tables are Parquet on Garage with their
  transactional catalog in PostgreSQL; DuckDB is the query/compiler process,
  not a durable database file or separate copy of the data.
- Resolver episode identity is a replaceable proposal product. A separately
  versioned automatic policy admits proposals before canonicalization; the
  Phase D policy intentionally admits every resolver proposal. Human binding
  decisions are the approved transactional exception: the small
  `binding_overrides` relation lives directly in PostgreSQL so partial unique
  indexes can enforce active locator and capture heads. Canonicalization applies
  an active override (including an explicit unbind) before choosing the latest
  automatically accepted source capture for each locator.
- Partitioning should reflect a semantically useful recomputation and backfill
  boundary. Keep lower-granularity IDs as row-level provenance when making them
  partitions would harm navigation or create needless orchestration overhead.
- Preserve strong known grouping hints such as AniList ID in keys or metadata
  when doing so improves operation and does not falsely promote an inference to
  accepted identity.

### Resist service and API sprawl

The API surface is already large enough that adding another microservice is no
longer a neutral choice. Do not create a service merely to make internal bronze
or silver data queryable to another repository component. Prefer data-layer
assets, durable lake artifacts, shared contracts, and embedded/local query
engines unless there is a concrete application or operational requirement for
an always-on API.

Existing narrow mirrors and metadata bridges—such as Kitsunekko and anime ID
crosswalk wrappers—may remain as-is while they are useful. Consolidate them only
when a real migration benefit justifies the disruption. New human-facing APIs
should normally expose gold contracts rather than raw lake internals.

Escape hatches are allowed. Interactive tools, experiments, latency-sensitive
paths, and one-off workflows may temporarily perform direct transformations or
read lower layers. Keep the boundary visible, document why it is an exception,
and promote stable reusable behavior into the data layer before multiple
callers depend on it.

### Architectural decision protocol

The user wants strong architectural recommendations, not automatic deference,
but consequential data-layer choices require informed sign-off. Before treating
a high-level choice as settled, explain:

1. the concrete user/domain problem being solved;
2. the proposed end-to-end data flow and ownership boundaries;
3. where each unit of computation executes;
4. which durable artifacts it produces and where they live;
5. how later steps query, join, or consume those artifacts;
6. operational and maintenance costs;
7. credible alternatives and why the recommendation wins; and
8. which conclusions come from measured corpus evidence versus assumptions.

Lead with the recommendation and work backward from the problem. Clearly label
repository facts, existing proposals, new recommendations, and approved
decisions. Proposed plan documents provide context; they are not automatically
approved architecture. Do not respond to disagreement by reflexively abandoning
a recommendation: reassess the evidence, then defend it concretely or explain
why another choice is better. The user is learning parts of this stack, so
define framework concepts in terms of this media pipeline before relying on
framework jargon.

## File Size Limits — This Must Never Be Allowed to Happen Again™

Large files conceal missing boundaries and make review, testing, and reuse
needlessly difficult. Treat line count as an architectural smoke alarm.

- **300 lines is the soft limit.** When a source file crosses 300 lines, stop
  and actively look for a coherent extraction: a reusable widget, domain
  module, application service, adapter, parser, or focused test module.
- **500 lines is the hard limit.** Do not create or enlarge a source file past
  500 lines. Stop the work and refactor before adding more behavior.
- If an existing file is already over 500 lines, any task touching it must
  leave it smaller unless the user explicitly scopes the work otherwise.
- Generated files, lockfiles, vendored code, fixtures, and machine-produced
  migrations are exempt.
- Quick-and-dirty one-file utilities under `scripts/` are temporarily exempt
  when they are intentionally exploratory or operational glue. Keep the
  contract clear at the top of the file, and promote stable behavior into a
  package module before other code depends on it.
- Declarative artifacts whose owning tool requires or conventionally exports
  one monolithic document—such as Grafana dashboard JSON—may also exceed these
  limits when splitting the file would make it invalid, non-importable, or
  substantially harder to use. Keep such exceptions narrowly scoped, call
  them out in the handoff, and prefer a composable source format if the
  artifact becomes something humans edit frequently.
- Hand-written application code, tests, ordinary configuration, and scripts
  outside the temporary `scripts/` exemption remain subject to the limits.
- Do not game the limit with compressed formatting, giant functions, or
  meaningless file splits. Extract by responsibility and keep the resulting
  interfaces explicit.

**Agents: call out limit violations loudly in progress updates and final
handoffs. A >500-line hand-written file is a stop-the-line architectural
failure, not a harmless style nit.**

## Deployment & Infrastructure

The services are typically deployed as a suite of containers coordinated by `compose.yaml` in the root.

- **Remote infrastructure operations are user-owned.** Under no circumstances
  should an agent proactively open an administrative shell/session on a host,
  hypervisor, or guest; inspect remote containers; alter host or cluster
  configuration; or deploy/restart services. Do not interpret requests to
  investigate, fix, remediate, or verify an application as authorization for
  host-level access or deployment. Prepare and validate repository changes
  locally, then give the user the commands or handoff needed for remote
  infrastructure operations. Connecting to a configured application or data
  service is not, by itself, a host-level infrastructure operation; the
  data-plane rules below govern those connections.
- **User-requested API smoke tests are allowed.** Agents may make
  application-level HTTP/API requests to user-specified service URLs for client
  validation and smoke testing when the user explicitly asks for that test.
  Keep these calls limited to the documented API behavior under test, and do
  not treat API access as permission to inspect or operate the remote host
  itself.
- **Configured data-plane reads are allowed.** Agents may use repository clients
  and configured credentials to perform non-mutating reads against development
  or production application data services when relevant to the task. This
  includes PostgreSQL connection checks and `SELECT`/catalog queries, and
  S3-compatible `LIST`, `HEAD`, and `GET` operations. These reads do not require
  separate live-smoke authorization merely because the service runs on another
  machine. Continue to avoid sensitive system catalogs, credential tables,
  private user data unrelated to the task, and unnecessarily broad result
  dumps. Use bounded projections/counts instead of `SELECT *` when output could
  expose sensitive or voluminous data.
- **Additive development migrations are allowed.** When implementation work
  includes a schema change, agents may run checked-in, non-destructive migrations
  against the configured development database. Allowed operations include
  creating new application tables, indexes, constraints, and adding compatible
  columns. Review the rendered migration first. Production migrations always
  remain user-owned unless the user separately and explicitly authorizes that
  exact production migration.
- **Remote data writes are not implied.** Read access and additive development
  migration permission do not authorize application `INSERT`, `UPDATE`,
  `DELETE`, `COPY FROM`, object upload/overwrite/delete, remote file edits,
  destructive or compatibility-breaking DDL, or test fixtures written to a
  shared database. `DROP`, `TRUNCATE`, destructive `ALTER`, database resets, and
  bulk rewrites require separate explicit authorization even in development.
  Database/role/principal creation, grants, credential creation/rotation, and
  secret inspection remain user-owned. Passing an existing configured secret
  opaquely to its intended client is allowed; printing, parsing for disclosure,
  or modifying the secret is not.
- These remote-data restrictions do not prohibit ordinary edits to repository
  files when the user asks to build or change code; they govern external data
  services and remote machine state.
- **Local Docker is allowed.** Agents may build, run, restart, inspect, and test
  containers on the current development machine when useful for validation.
  Keep local validation clearly distinguished from remote deployment.
- **Local Stack**: When running via Docker Compose, the primary entry point is `http://localhost:8080`.
- **macOS Note**: If using OrbStack or Docker Desktop on Mac, verify active containers with `docker ps` to confirm port mapping.
- **Gateway**: The `site/Caddyfile` defines the unified routing. It serves the static docsite and reverse-proxies `/api/v1/*` requests to the backend services (e.g., `anime-crosswalk` and `kitsunekko-subtitles`).


## Repo structure

See [docs/monorepo-philosophy.md](docs/monorepo-philosophy.md) for the full rationale if needed.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the durable ASR/config/backend boundaries.

**Documentation Strategy:**
- **User/Developer facing content** (guides, setup, references) lives in `site/src/content/docs/`. This is the default place for documenting new features.
    - NOTE! This uses Astro Starlight, so the page title in frontmatter is shown by default. Only write headings at H2 or below, don't write a page title as this is redundant.
- **Internal design/architectural notes** live in `docs/`.

```text
.
├── compose.yaml           # Docker orchestration for the full service stack
├── AGENTS.md              # Agent guidelines and repo map
├── docs/                  # Internal design & architectural notes
├── site/                  # User-facing documentation site (Astro/Starlight)
│   └── Caddyfile          # Unified API Gateway & Static site config
├── packages/              # Shared libraries (workspace members)
│   ├── core/              # Shared contracts, config, and transcript formats
│   └── frontend/          # CLI entrypoints and TUI surfaces
├── envs/                  # Platform-specific runtimes & dependencies
│   ├── apple/             # MacBook workflows (MLX, Metal, local ASR/VAD)
│   ├── cuda/              # Nvidia workstation workflows (CUDA ASR)
│   └── services/          # Service deployments (Kitsunekko API, etc.)
├── examples/              # Fixtures and sample media for smoke-testing
└── pyproject.toml         # Workspace coordination
```

---
## Toolchain

### Python

This repo uses Astral uv.

**Always work from an environment that provides the dependencies required for your task.**

#### 1. Lightweight / Frontend Tools
For TUI surfaces, simple file management, or subtitle alignment, use the `packages/frontend` environment. These tools do not require ML dependencies.
```sh
cd packages/frontend
uv sync
uv run ja-media subsync tui --help
```

#### 2. Heavyweight / ML Runtimes
For transcription, VAD, and other audio-processing tasks, use the platform-specific runtime environment (e.g., `envs/apple` for MacBooks). Use this when developing the backend logic itself.
```sh
cd envs/apple
uv sync
uv run ja-media transcribe episode.mp3
```

#### 3. Testing Integration (The "Tool Shape")
If you need to test the `ja-media` CLI as a user would (integrating the frontend and the ML backend), use the `[apple]` extra from the frontend package. This mirrors the persistent install shape described in [docs/uv-tool-install-frontends.md](docs/uv-tool-install-frontends.md).

Example smoke-test with JFK fixture:
```sh
cd packages/frontend
uv run --isolated --with-editable '.[apple]' ja-media transcribe --startup-only ../../examples/input/jfk.wav
```

#### 4. Running Tests
`pytest` is a declared dev dependency. Do not use ad hoc `uv run --with pytest ...` invocations unless you are intentionally testing outside the repo environments.

For workspace packages (`packages/core`, `packages/media`), run tests from the repo root:
```sh
uv run pytest packages/core/tests
```

For standalone environments that are not root workspace members, run from that environment:
```sh
cd packages/frontend
uv run pytest tests

cd envs/apple
uv run pytest tests

cd envs/services
uv run pytest tests
```

**Platform Verification**: Check you're on the right box before running heavyweight tools. If a task requires CUDA, verify the machine has it (e.g., `nvidia-smi`). If it requires MLX/Metal, verify you're on Apple Silicon.

- Add dependencies with `uv add` **from within the relevant directory**.
- **Never** edit `pyproject.toml` directly to add dependencies.
- NEVER run a script using `python` or `python3`. Always use `uv run` from the correct directory.

Prefer tomllib + Pydantic Settings for configuration where possible.

---
## System

Assume you have (at a minimum):
- curl
- ffmpeg, ffprobe, etc
- gh
- jq
- rg

## Secrets

Secrets are in `.env` in repo root. NEVER read this file directly with `cat`,
`sed`, `rg`, editors, or any other content-printing tool. If you need to check
that it exists, `stat .env`.

When a command needs project environment variables, it is the agent's job to
load them. Source `.env` inside the shell command or use idiomatic tooling
(for example, python-dotenv for Python) so values are available to the process
without being printed. Do not ask the user to export variables that already
belong in repo `.env`.

If an env handoff file is needed for a subprocess, put it in `/tmp` or another
gitignored location, avoid echoing secret values into logs, and clean it up
afterward.

## Services

First-party LAN APIs live in `envs/services`, are assembled by the owning
Compose deployment, and are exposed through stable `/api/v1/*` routes in
`site/Caddyfile`. Lightweight client contracts and HTTP SDKs live in
`packages/core`; tools should use those clients rather than construct service
URLs themselves.

Service URLs are **not** secrets and do not belong in `.env`. Clients resolve a
service-specific override first, then fall back to `[services].root_url` in
`~/.config/ja-media-toolkit/config.toml`. See
[site/src/content/docs/setup/config.md](site/src/content/docs/setup/config.md).

When the user asks to test against "live", "prod", "remote", "LAN", or
"tailnet", use the
[live-service-smoke skill](.agents/skills/live-service-smoke/SKILL.md). Assume
the user has already configured the tailnet service root in system config unless
config discovery proves otherwise. Use SDK clients or derive curl bases from
config at runtime; never hard-code private service URLs into repo files.

When adding or substantially changing a service, use
[the add-service skill](.agents/skills/add-service/SKILL.md). It covers the
complete vertical slice: runtime, core SDK, tests, Compose/Caddy integration,
`/healthz`, `/metrics`, Prometheus discovery, and docsite updates.
