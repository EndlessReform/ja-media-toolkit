---
title: Setup CLI Tools
description: Install the ja-media command-line interface for use across your system.
---

Install the `ja-media` toolkit as a persistent command to run tools like `transcribe` and `subsync` from any directory.

## Prerequisites

- **uv**: The Astral `uv` package manager.
- **ffmpeg**: Must be available on your system `PATH`.
- **Services**: Many CLI tools depend on background services. See [Setting Up Services](/setup/services) before continuing.

## Installation

Install the toolkit as a `uv tool` to keep its dependencies isolated from your global Python environment.

### Install from the main branch

```sh
uv tool install --python 3.13 \
  'ja-media-frontend[apple] @ git+ssh://git@github.com/EndlessReform/ja-media-toolkit.git@main#subdirectory=packages/frontend'
```

### Install a specific version or commit

Replace `@main` with a tag or commit hash:

```sh
uv tool install --python 3.13 \
  'ja-media-frontend[apple] @ git+ssh://git@github.com/EndlessReform/ja-media-toolkit.git@<branch-or-commit>#subdirectory=packages/frontend'
```

### Updating

Use the `--force` flag to update to a newer reference:

```sh
uv tool install --force --python 3.13 \
  'ja-media-frontend[apple] @ git+ssh://git@github.com/EndlessReform/ja-media-toolkit.git@main#subdirectory=packages/frontend'
```

## Verifying the Installation

```sh
ja-media --help
```

### Implementation Details

- `ja-media-frontend[apple]`: Installs the shared CLI frontend and Apple-specific backend dependencies (required for ASR and VAD).
- `#subdirectory=packages/frontend`: Directs `uv` to the installable package within the repository.
