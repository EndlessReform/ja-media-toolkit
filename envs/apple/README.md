# Apple Environment Developer Notes

This environment owns Mac-local implementation details: MLX-backed experiments,
VibeVoice audio-side preprocessing, and dependencies that should not leak into
the cheap root workspace.

For user-facing `ja-media` commands, start with [../../README.md](../../README.md).
This file is for maintaining the Apple environment itself.

## Setup

```sh
cd envs/apple
uv sync
```

Run commands from this directory while developing the Apple backend. The root
project is only for workspace coordination and shared package editing.

## VibeVoice Apple Backend

The Apple `transcribe` implementation loads a local VibeVoice audio
encoder/projector bundle, uses VAD to plan source-aligned ASR chunks when needed,
and sends mixed Chat Completions `prompt_embeds` to the configured vLLM server.

The local machine never loads the decoder weights. It does need this env's audio
side dependencies and access to the configured checkpoint.

Developer smoke-test config, imports, and model startup without calling vLLM:

```sh
uv run ja-media transcribe -c config.local.toml ../../examples/input/jfk.wav \
  --startup-only \
  --format text
```

Use the root README for normal transcription and VAD command examples.

## MLX-Audio Pin

This environment currently pins `mlx-audio` to upstream git commit
`f7c11556eda88731be5cc75ddbdf4a4cb9eeaafc`.

Why: the PyPI `mlx-audio==0.4.3` package does not include
`mlx_audio.vad.models.silero_vad`, even though the `mlx-community/silero-vad`
model card documents:

```python
from mlx_audio.vad import load

model = load("mlx-community/silero-vad")
timestamps = model.get_speech_timestamps("audio.wav", return_seconds=True)
```

Upstream `mlx-audio` added that missing implementation after the PyPI release in
PR #701 / commit `5902067` (`Add Silero VAD model`). The git pin should be
replaceable with a normal PyPI dependency after the next release containing that
code.

## Library Docs

See [../../docs/vad.md](../../docs/vad.md) for the VAD library API and pipeline
usage.
