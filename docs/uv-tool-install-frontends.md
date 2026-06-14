# uv tool installs, frontends, and environment extras

This note records the packaging shape for installing the shared `ja-media`
frontend from this Git monorepo. The goal was to answer a practical question:

> Can one shared `ja-media` frontend choose an Apple or CUDA implementation with
> extras, instead of copying the CLI into every `envs/*` package?

The short answer is yes, with one important boundary: keep the cheap root
workspace focused on shared contract packages. Let runnable environments and
tool-style installs resolve the heavier backend graph separately.

## The uv concepts

uv has three different ideas that are easy to blur together:

1. A **project** is one `pyproject.toml`. `uv sync` creates or updates that
   project's `.venv`.
2. A **workspace** is a group of projects locked together. The uv docs describe
   workspaces as useful when packages are developed together and share one
   lockfile.
3. A **tool install** installs console scripts from one package into a persistent
   isolated environment. It is closer to `pipx install` than to `uv sync`.

The docs worth reading are:

- [Tools](https://docs.astral.sh/uv/concepts/tools/)
- [Using tools](https://docs.astral.sh/uv/guides/tools/)
- [Workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- [Dependency sources](https://docs.astral.sh/uv/concepts/projects/dependencies/)
- [`uv sync`](https://docs.astral.sh/uv/reference/cli/#uv-sync)
- [`uv tool install`](https://docs.astral.sh/uv/reference/cli/#uv-tool-install)

The key mental model: `uv tool install` installs **one package's scripts**. It
does not install "the workspace" as a first-class thing. If a package lives in a
Git monorepo subdirectory, the package spec points at that subdirectory.

For example, this is the validated Git install shape:

```sh
uv tool install --python 3.13 \
  'ja-media-frontend[apple] @ git+ssh://git@github.com/EndlessReform/ja-media-toolkit.git@<branch-or-commit>#subdirectory=packages/frontend'
```

Use a branch, tag, or full commit hash that already exists on the remote. uv
will fetch the repository, enter `packages/frontend`, and resolve the
`ja-media-frontend[apple]` package from there.

For local development, run frontend-owned commands from the frontend package:

```sh
cd packages/frontend
uv run ja-media subsync tui --help
```

Run Apple-runtime commands from the Apple environment:

```sh
cd envs/apple
uv run ja-media transcribe --help
```

To smoke-test the persistent install shape from a checkout, this equivalent
command also works:

```sh
cd packages/frontend
uv run --isolated --with-editable '.[apple]' ja-media transcribe --help
```

## Package shape

I added one deliberately small frontend package:

```text
packages/frontend/
├── pyproject.toml
└── src/ja_media_frontend/cli.py
```

That package owns the `ja-media` parser and console script:

```toml
[project.scripts]
ja-media = "ja_media_frontend.cli:main"
```

The Apple package still owns the actual Apple work:

- ASR config loading
- VAD-backed chunk planning
- VibeVoice/vLLM backend construction
- local VAD execution

The shared frontend package owns cheap user-facing surfaces that do not need
Apple-only dependencies, including the subtitle TUI.

The frontend imports Apple code lazily, only after a command needs it. That
means `ja-media --help` can run from the frontend package without importing MLX,
Torch, or the Apple backend modules.

The frontend also has an Apple extra:

```toml
[project.optional-dependencies]
apple = [
    "ja-media-apple",
]
```

Locally, that extra points back to `envs/apple` with `tool.uv.sources`.

## Why the root workspace changed

The first attempt left the root workspace as:

```toml
[tool.uv.workspace]
members = [
  "packages/*",
]
```

That made uv treat `packages/frontend` as part of the root workspace. Then uv
tried to solve this path:

```text
root workspace
  -> ja-media-frontend[apple]
    -> ja-media-apple
      -> ja-media-media / ja-media-core / ja-media-transcripts
```

That failed because the same shared packages were visible through two different
source routes:

- as root workspace members;
- as editable path sources declared by `envs/apple`.

uv quite reasonably rejected that, because a resolver needs one unambiguous
source for each package.

The fix was to make the root workspace explicit:

```toml
[tool.uv.workspace]
members = [
  "packages/core",
  "packages/media",
  "packages/transcripts",
]
```

This keeps the root lockfile cheap and contract-focused. The frontend is still a
package in the repo, but it is not part of the shared root workspace lock.
The root project itself should not depend on `ja-media-frontend`; otherwise uv
will try to make the root lockfile own user-facing runtime dependencies again.

## What passed locally and from Git

From `envs/apple`, the normal Apple env still syncs:

```sh
uv sync --locked
```

The Apple env's installed `ja-media` command now resolves to the shared
frontend:

```sh
uv run ja-media --help
uv run ja-media transcribe --help
```

The frontend package with the Apple extra also resolved in a dry-run sync:

```sh
uv sync --project ../../packages/frontend --extra apple --dry-run
```

And the isolated, tool-like run worked:

```sh
uv run --isolated --with-editable '../../packages/frontend[apple]' ja-media --help
```

Finally, an Apple-backed command reached the Apple implementation through the
frontend:

```sh
uv run --isolated --with-editable '../../packages/frontend[apple]' \
  ja-media vad-local /tmp/ja-media-toolkit-missing.wav
```

That command failed because the file was intentionally missing, but the
traceback showed execution had reached `ja_media_apple.cli.run_vad_local`.

The real remote tool install also worked:

```sh
uv tool install --python 3.13 \
  'ja-media-frontend[apple] @ git+ssh://git@github.com/EndlessReform/ja-media-toolkit.git@<commit>#subdirectory=packages/frontend'
```

uv built these repo packages from the same Git ref:

- `ja-media-frontend` from `packages/frontend`
- `ja-media-apple` from `envs/apple`
- `ja-media-core` from `packages/core`
- `ja-media-media` from `packages/media`
- `ja-media-transcripts` from `packages/transcripts`

The installed executable was:

```sh
ja-media
```

And this smoke test passed:

```sh
ja-media --help
```

## What this means for CUDA

The CUDA package should mirror the Apple package at the dependency boundary, not
by copying the CLI.

The shape should be:

```text
packages/frontend          shared parser and user-facing command names
envs/apple                 Apple backend implementation package
envs/cuda                  CUDA backend implementation package
packages/core              stable ASR/VAD/audio contracts
packages/media             media helpers
packages/transcripts       transcript helpers
```

Then `packages/frontend` can grow:

```toml
[project.optional-dependencies]
apple = ["ja-media-apple"]
cuda = ["ja-media-cuda"]
```

If Apple and CUDA dependencies cannot be installed together, uv has a
`tool.uv.conflicts` setting for mutually exclusive extras. That should only be
added once both extras exist and actually conflict.

## Practical rule of thumb

Use `uv sync` from an `envs/*` directory when developing or running real jobs:

```sh
cd envs/apple
uv sync
uv run ja-media transcribe episode.mp3
```

Use a frontend extra when testing the install shape:

```sh
cd envs/apple
uv run --isolated --with-editable '../../packages/frontend[apple]' ja-media --help
```

Use `uv tool install` when you want a persistent command outside the project
venv. The package you install should be the frontend package, and the selected
backend should come through an extra or an additional requirement.

This keeps the permanent interface in one place while leaving the volatile model
runtime dependencies in the environment packages where they belong.
