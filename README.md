# ja-media-toolkit

Tools for organizing and processing Japanese media as a language learner:
audio ingest, VAD, ASR chunk planning, transcript alignment, and related local
workflows.

## Where To Start

- [Monorepo philosophy](docs/monorepo-philosophy.md): package/env layout.
- [uv tool installs](docs/uv-tool-install-frontends.md): installing the shared
  `ja-media` frontend as a persistent command with an environment extra.
- [VAD](docs/vad.md): library usage, split planning, and local smoke tests.
- [Apple environment](envs/apple/README.md): Mac-local commands, including the
  current `vad-local` convenience entrypoint.

## Persistent CLI Install

For normal development, prefer `cd envs/apple && uv run ja-media ...`; that uses
the checked-out source tree and the Apple env lockfile.

When you want a persistent `ja-media` command outside the repo venv, install the
shared frontend package from Git and select the Apple backend with the
`[apple]` extra:

```sh
uv tool install --python 3.13 \
  'ja-media-frontend[apple] @ git+ssh://git@github.com/EndlessReform/ja-media-toolkit.git@<branch-or-commit>#subdirectory=packages/frontend'
```

Then smoke-test the installed tool:

```sh
ja-media --help
```

The important pieces are:

- `ja-media-frontend[apple]`: install the shared CLI frontend plus the Apple
  backend dependencies.
- `@ <branch-or-commit>`: pin to a branch, tag, or commit that exists on the
  remote.
- `#subdirectory=packages/frontend`: tell uv the installable package lives below
  the repository root.

## Quick VAD Smoke Test

```sh
cd envs/apple
uv sync
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --split-every-minutes 10 \
  --split-radius-s 60 \
  --format text
```

The Apple environment currently pins upstream `mlx-audio` from git because the
released PyPI package lacks the Silero VAD implementation documented by the
model card.
