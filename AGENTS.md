# ja-media-toolkit

This repo is a collection of tools to assist me (a native English speaker, JLPT ~low N3) with organizing native content.

## Project goals

Here are some common tasks I as a language learner might need done on raw media files:
- Batch renaming utilities: safely allow agents to bulk rename files to fit conventions, e.g. for Jellyfin or Mokuro, without inadvertently destroying files
- Compare existing community .srt files against the actual voice activity in an audio / video file, to see which one has the best timing 
- Generate `.srt` subtitles for longer files, eg radio/podcast .mp3s, which might not have official subtitles 
    - Compare against known file metadata to correct for transcription errors, mislabeled named entities, etc.
- Diarize audio to allow for tasks like separating out clips by speaker, visualization for shadowing, mining and analytics, etc.
- Run my own REST API atop a homelab Kitsunekko mirror, to allow for:
    - Offloading the upstream heavyweight git repo from edge clients like dev laptops
    - Easier search 
    - Using the transcript corpus for mining sentence examples

## Repo structure

See [docs/monorepo-philosophy.md](docs/monorepo-philosophy.md) for the full rationale.

```text
packages/          contracts and shared libraries (workspace members)
├── core/          shared types, config, manifests, job/result contracts
├── media/         filesystem/media utilities: renaming, probing, chunks
└── transcripts/   SRT/ASS parsing, normalization, alignment, scoring
envs/              runnable environments with platform-specific dependencies
├── apple/         MacBook workflows: MLX, Metal, local experiments
├── cuda/          Nvidia workstation workflows: CUDA ASR, VAD, diarization
└── services/      LXC/server workflows: Kitsunekko API, indexes, plugins
docs/              design notes and references
pyproject.toml     workspace coordination only (no ML deps)
```

## Toolchain
### Python

This repo uses Astral uv.

**Always work from an `envs/` directory, never from the root.** Each environment has its own `.venv` and dependency set. The root should only be used for editing shared code in `packages/`.

```sh
cd envs/apple
uv sync
uv run ja-media transcribe episode.mp3
```

**Check you're on the right box before running anything.** If a task requires CUDA, verify the machine has it (e.g. `nvidia-smi`). If it requires MLX/Metal, verify you're on Apple Silicon. Running an environment on the wrong platform will fail or silently produce wrong results.

- Add dependencies with `uv add` **from within the relevant `envs/` directory**.
- **Never** add a dependency by:
    - Editing `requirements.txt` or `requirements.in` (neither of these files exist!)
    - Editing `pyproject.toml` directly (you can _check_ here though if something is installed)
    - Directly running `pip install`
- NEVER run a script using `python` or `python3`. Always use `uv run` from the correct environment.


## Useful scratch files for smoke testing
