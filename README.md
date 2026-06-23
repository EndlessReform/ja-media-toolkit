# ja-media-toolkit

A collection of tools for organizing and processing Japanese media as a language learner. This repo provides a pipeline for audio ingest, Voice Activity Detection (VAD), ASR chunk planning, transcript alignment, and metadata bridging.

## What is this?

The toolkit is split into two primary categories: **Tools** and **Services**.

### Tools (CLI)
User-facing CLI utilities used for processing local media. Examples include:
- `transcribe`: Hybrid local/remote ASR to generate transcripts from audio.
- `subsync`: TUI and utilities for aligning subtitle files to audio.
- `vad-local`: Voice activity detection for planning ASR chunks.

### Services (APIs)
Background services that provide data or compute resources, often deployed in containers:
- **Kitsunekko Mirror**: A local mirror of subtitle datasets.
- **Anime Crosswalk**: A metadata service bridging IDs across TVDB, TMDB, MAL, and AniList.
- **ASR Backends**: Remote vLLM servers providing the decoding logic for transcription.

### Documentation
- **User Guides**: Detailed setup and usage instructions are located in the docsite (`site/src/content/docs`).
- **Internal Architecture**: Design notes, monorepo philosophy, and system contracts live in the `/docs` directory.

---

## Repository Structure

```text
.
├── docs/               # Internal design & architectural notes (The "Why")
├── site/               # User-facing documentation site (The "How")
├── packages/           # Shared logic and shared libraries
│   ├── core/           # Shared contracts, transcript formats, and config
│   └── frontend/       # CLI entrypoints and TUI surfaces
├── envs/               # Platform-specific runtimes & dependencies
│   ├── apple/          # MacBook workflows (MLX, Metal, local ASR/VAD)
│   ├── cuda/           # Nvidia workstation workflows (CUDA ASR)
│   └── services/       # Service deployments (Kitsunekko API, etc.)
└── AGENTS.md           # Guidelines for AI agents operating in this repo
```

---

## Local Development

This repository is a monorepo managed with **Astral [uv](https://docs.astral.sh/uv/)**. The root is for coordination; runnable code lives in `packages/` and `envs/`.

### Running Tools from Checkout
To ensure the correct dependencies and virtual environment are used, always `cd` into the package or environment that owns the tool.

**Frontend & TUI Tools** (e.g., `subsync`)
```sh
cd packages/frontend
uv sync
uv run ja-media subsync tui --help
```

**Audio & ML Runtimes** (e.g., `transcribe`)
These tools require a configuration file specifying your vLLM server and model paths.
```sh
cd envs/apple
uv sync

# Create a local config for testing
echo '[asr.backends.local]
type = "vibevoice_vllm"
vllm_base_url = "http://localhost:8000"
vllm_model = "your-model-id"
checkpoint = "jkeisling/vibevoice-encoder-only"' > config.local.toml

# Run a smoke-test on a sample file
uv run ja-media transcribe -c config.local.toml ../../examples/input/jfk.wav --format text
```

### Architecture & Design
For developers, the following files are essential for understanding the system boundaries:
- `docs/monorepo-philosophy.md`: Why we separate `packages/` from `envs/`.
- `docs/ARCHITECTURE.md`: The durable boundaries between ASR, config, and backends.
- `docs/audio-library/README.md`: Derived anime audio filesystem, metadata, and conversion contracts.
- `AGENTS.md`: Guidelines for LLMs and agents operating within this codebase.
