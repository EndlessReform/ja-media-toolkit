# ja-media-toolkit

This repo is a collection of tools to assist me (a native English speaker, JLPT ~low N3) with managing native content.

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

## Philosophy

1. **Avoid enterprise brainrot.** Remember that these tools are primarily for my use case and my system: anything useful to the community will be broken out into its own project. So avoid premature abstraction: e.g., don't bother making anything work for Windows, since I don't use it, or for AMD, since I don't care. Focus only on the abstractions that cover the volatility I might actually see; e.g. below:
2. **Data and contracts are permanent, models are ephemeral.** Models will change, runtimes will change, the location of compute will change (laptop vs workstation vs serverless GPU vs GPU server). Decouple _what_ should be done from _who_ is doing it.
3. **Use liberal documentation.** I am using this repo (in part) to learn best practices for system design and audio management: so use a literate style.
    - Ensure all core abstractions have descriptive docstrings. (no need to comment every line though).
    - Explain _why_ key decisions were made
    - Ensure config has nontrivial examples

## Environment

TODO fill this in more.


## Repo structure

See [docs/monorepo-philosophy.md](docs/monorepo-philosophy.md) for the full rationale if needed.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the durable ASR/config/backend boundaries.

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

Prefer tomllib + Pydantic Settings for configuration where possible.

## Smoke-testing

### About the system

- Assume both macOS + Linux workstations have `ffmpeg` + `ffprobe` installed.

### Current VAD implementation notes

- VAD core contracts live in `packages/core`; model/runtime dependencies belong
  in `envs/*`.
- Keep `AudioChunk` lightweight. It describes source coordinates and metadata;
  decoded arrays belong in `InMemoryAudioChunk`.
- Use `materialize_audio_chunk` / an ingestor layer for decoding instead of
  adding sample arrays to `AudioChunk`.
- The Apple VAD path currently uses `mlx-community/silero-vad` through
  `envs/apple`, with `mlx-audio` pinned to upstream git until PyPI includes the
  Silero VAD implementation.
