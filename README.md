# ja-media-toolkit

Tools for organizing and processing Japanese media as a language learner:
audio ingest, VAD, ASR chunk planning, transcript alignment, and related local
workflows.

## Where To Start

- [Monorepo philosophy](docs/monorepo-philosophy.md): package/env layout.
- [VAD](docs/vad.md): library usage, split planning, and local smoke tests.
- [Apple environment](envs/apple/README.md): Mac-local commands, including the
  current `vad-local` convenience entrypoint.

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
