# Monorepo philosophy

The simple mental model:

```text
packages/ = contracts and shared libraries
envs/     = runnable environments with platform-specific dependencies
root      = coordination only
```

Do not make one package for every tiny feature. Do not make the root environment
install every possible backend. Do not couple CUDA to benchmarking. CUDA is a
platform capability, not a workflow.

You work from an environment:

```sh
cd envs/apple
uv sync
uv run ja-media transcribe episode.mp3
```

or:

```sh
cd envs/cuda
uv sync
uv run ja-media transcribe episode.mp3
uv run ja-media benchmark-asr diet-corpus.yaml
```

Same repo, different uv environment, different dependency set.

## Directory Shape

Recommended initial shape:

```text
.
├── packages/
│   ├── core/          # shared contracts, config, and transcript formats
│   └── media/         # filesystem/media utilities: renaming, probing, chunks
├── envs/
│   ├── apple/         # MacBook workflows: MLX, Metal, local experiments
│   ├── cuda/          # Nvidia workstation workflows: CUDA ASR, VAD, diarization
│   └── services/      # LXC/server workflows: Kitsunekko API, indexes, plugins
├── docs/
└── pyproject.toml
```

Add more packages only when there is a real shared boundary. A package should
exist because multiple environments need the same code, not because a feature
name exists.

Good package boundaries:

- `core`: dataclasses, protocols, path conventions, manifests, artifact records,
  job/result schemas, and lightweight transcript/subtitle processing.
- `media`: local file operations, safe renaming plans, ffmpeg/probing wrappers,
  chunk manifests.

Avoid early package explosions like:

- `vad-core`
- `asr-core`
- `diarization-core`
- `apple-core`
- `cuda-core`
- `benchmark-core`

Those can wait until the codebase proves they are real independent libraries.

## Root Rules

The root `pyproject.toml` should stay light. It can define the workspace and
maybe lightweight dev tooling, but it should not install ML stacks by default.

The root should not depend on:

- `mlx-audio`
- `torch`
- `vllm`
- CUDA-specific wheels
- service-only dependencies
- benchmark-only dependencies

Move the current root `mlx-audio` dependency into `envs/apple`.

The root workspace can include the shared packages:

```toml
[tool.uv.workspace]
members = [
  "packages/core",
  "packages/media",
]
```

The root is for editing shared contracts. Environments are for running real
jobs. See [uv tool installs, frontends, and environment extras](uv-tool-install-frontends.md)
for why the shared CLI frontend intentionally stays outside this cheap root
workspace.

## Environment Rules

Each `envs/*` directory is its own uv project with its own `pyproject.toml` and
`uv.lock`. It depends on shared repo packages by path.

Example `envs/apple/pyproject.toml`:

```toml
[project]
name = "ja-media-apple"
requires-python = ">=3.13"
dependencies = [
  "ja-media-core",
  "ja-media-frontend",
  "ja-media-media",
  "mlx-audio",
]

[project.scripts]
ja-media = "ja_media_frontend.cli:main"

[tool.uv.sources]
ja-media-core = { path = "../../packages/core", editable = true }
ja-media-frontend = { path = "../../packages/frontend", editable = true }
ja-media-media = { path = "../../packages/media", editable = true }
```

Example `envs/cuda/pyproject.toml`:

```toml
[project]
name = "ja-media-cuda"
requires-python = ">=3.13"
dependencies = [
  "ja-media-core",
  "ja-media-frontend",
  "ja-media-media",
  "torch",
  "vllm",
]

[project.scripts]
ja-media = "ja_media_frontend.cli:main"

[tool.uv.sources]
ja-media-core = { path = "../../packages/core", editable = true }
ja-media-frontend = { path = "../../packages/frontend", editable = true }
ja-media-media = { path = "../../packages/media", editable = true }
```

Example `envs/services/pyproject.toml`:

```toml
[project]
name = "ja-media-services"
requires-python = ">=3.13"
dependencies = [
  "ja-media-core",
  "fastapi",
  "uvicorn",
]

[project.scripts]
kitsunekko-api = "ja_media_services.kitsunekko_api:main"

[tool.uv.sources]
ja-media-core = { path = "../../packages/core", editable = true }
```

This keeps dependency ownership obvious:

- Apple-specific dependencies live in `envs/apple`.
- CUDA dependencies live in `envs/cuda`.
- service dependencies live in `envs/services`.
- shared packages stay cheap to install.

## Contracts, Not Frameworks

Shared packages define contracts. Environments implement them.

For example, `packages/core` might define:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass(frozen=True)
class TranscriptionJob:
    input_path: Path
    language: str = "ja"
    model: str | None = None

@dataclass(frozen=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str

@dataclass(frozen=True)
class Transcript:
    segments: list[TranscriptSegment]

class Transcriber(Protocol):
    def transcribe(self, job: TranscriptionJob) -> Transcript:
        ...
```

Then:

- `envs/apple` implements that contract with MLX.
- `envs/cuda` implements that contract with CUDA libraries.
- `envs/services` probably does not implement it at all.

The contract lets workflows share inputs, outputs, manifests, scoring, and file
formats without forcing every machine to install every backend.

## Workflows

### Transcribe locally on Mac

```sh
cd envs/apple
uv sync
uv run ja-media transcribe episode.mp3 --model mlx-whisper
```

Uses:

- shared contracts from `packages/core`
- file helpers from `packages/media`
- MLX dependencies from `envs/apple`

### Transcribe on Nvidia workstation

```sh
cd envs/cuda
uv sync
uv run ja-media transcribe episode.mp3 --model some-cuda-model
```

Uses:

- the same shared contracts
- CUDA dependencies from `envs/cuda`

This is not a benchmark-specific environment. It is the general Nvidia
workstation environment. Benchmarking is one workflow inside it.

### Run ASR benchmarks

```sh
cd envs/cuda
uv run ja-media benchmark-asr diet-corpus.yaml
```

Benchmark code can live in `envs/cuda` at first. If Apple and services also need
parts of it later, move the shared parts into `packages/core`.

### Run Kitsunekko service

```sh
cd envs/services
uv sync
uv run kitsunekko-api
```

Uses:

- shared transcript parsing and indexing code
- service dependencies
- no ML dependencies by default

### Rename files

This is pure shared/local functionality. It can run from any environment that
depends on `packages/media`.

```sh
cd envs/apple
uv run ja-media rename --dry-run /path/to/media
```

or:

```sh
cd envs/services
uv run ja-media rename --dry-run /mnt/library
```

## Remote Execution

Do not build a scheduler.

The first remote story should be boring:

```sh
rsync episode.mp3 workstation:/spool/ja-media/
ssh workstation 'cd /repo/envs/cuda && uv run ja-media transcribe /spool/ja-media/episode.mp3'
rsync workstation:/spool/ja-media/episode.transcript.json .
```

If this becomes repetitive, wrap those commands in a small CLI command in
`envs/apple`, such as:

```sh
uv run ja-media remote-transcribe episode.mp3 --host workstation
```

That wrapper can still just use SSH and rsync. No queue, no daemon, no web API
unless the pain is real and repeated.

## Promotion Rule

Start code in the environment that needs it. Promote code into `packages/` only
when another environment needs it too.

Examples:

- CUDA-only ASR adapter starts in `envs/cuda`.
- MLX-only VAD adapter starts in `envs/apple`.
- transcript scoring used by CUDA benchmarks and services moves to
  `ja_media_core.transcripts`.
- safe rename planning used everywhere moves to `packages/media`.
- shared job/result dataclasses move to `packages/core`.

This avoids premature package design while keeping a path out of copy-paste.

## Decision Rules

Put code in `packages/` when:

- it is backend-neutral;
- it is cheap to install;
- multiple environments need it;
- it defines shared contracts, formats, or reusable utilities.

Put code in `envs/apple` when:

- it needs MLX or Apple-specific assumptions;
- it is mainly for local MacBook workflows.

Put code in `envs/cuda` when:

- it needs CUDA, Torch, vLLM, or workstation assumptions;
- it may be used for ASR, VAD, diarization, embeddings, or benchmarks.

Put code in `envs/services` when:

- it is a persistent service;
- it belongs on Proxmox/LXC;
- it needs web/database/search dependencies;
- it should not install ML frameworks by default.

## Current Migration

1. Move `mlx-audio` out of the root `pyproject.toml`.
2. Create `envs/apple` and put `mlx-audio` there.
3. Keep only shared packages under `packages/`.
4. Keep backend-neutral transcript contracts and format helpers in
   `ja_media_core.transcripts`; keep runtime-specific transcription code inside
   `envs/apple` / `envs/cuda`.
5. Add `envs/cuda` when workstation work begins.
6. Add `envs/services` when Kitsunekko or media-server services begin.

The goal is not a perfect taxonomy. The goal is that installing one environment
does not accidentally install another machine's world.

## References

- uv workspaces: https://docs.astral.sh/uv/concepts/projects/workspaces/
- uv dependencies and dependency sources:
  https://docs.astral.sh/uv/concepts/projects/dependencies/
- uv project configuration:
  https://docs.astral.sh/uv/concepts/projects/config/
