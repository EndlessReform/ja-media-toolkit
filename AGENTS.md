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
- Declarative artifacts whose owning tool requires or conventionally exports
  one monolithic document—such as Grafana dashboard JSON—may also exceed these
  limits when splitting the file would make it invalid, non-importable, or
  substantially harder to use. Keep such exceptions narrowly scoped, call
  them out in the handoff, and prefer a composable source format if the
  artifact becomes something humans edit frequently.
- Hand-written application code, tests, scripts, and ordinary configuration
  remain subject to the limits.
- Do not game the limit with compressed formatting, giant functions, or
  meaningless file splits. Extract by responsibility and keep the resulting
  interfaces explicit.

**Agents: call out limit violations loudly in progress updates and final
handoffs. A >500-line hand-written file is a stop-the-line architectural
failure, not a harmless style nit.**

## Deployment & Infrastructure

The services are typically deployed as a suite of containers coordinated by `compose.yaml` in the root.

- **Remote operations are user-owned.** Under no circumstances should an agent
  proactively SSH into any machine, connect to a hypervisor or guest, inspect
  remote containers, alter remote infrastructure, or deploy/restart services.
  Do not interpret requests to investigate, fix, remediate, or verify a service
  as authorization for remote access or deployment. Prepare and validate the
  repository changes locally, then give the user the commands or handoff needed
  to perform remote operations themselves. This remains in force until these
  repository instructions explicitly say otherwise.
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

When adding or substantially changing a service, use
[the add-service skill](.agents/skills/add-service/SKILL.md). It covers the
complete vertical slice: runtime, core SDK, tests, Compose/Caddy integration,
`/healthz`, `/metrics`, Prometheus discovery, and docsite updates.
