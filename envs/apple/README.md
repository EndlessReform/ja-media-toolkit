# Apple Environment

This environment is for Mac-local workflows: lightweight audio utilities,
MLX-backed experiments, and client-local preprocessing before ASR.

Run commands from this directory:

```sh
cd envs/apple
uv sync
```

## Local VAD Smoke Test

`vad-local` is an Apple-only convenience command for trying VAD parameters on a
local file. It is intentionally not the final cross-environment CLI.

```sh
uv run ja-media vad-local ../../examples/input/jfk.wav --format text
```

Example output from the checked-in JFK fixture:

```text
source: /Users/ritsuko/projects/ai/audio/ja-media-toolkit/examples/input/jfk.wav
model: mlx-community/silero-vad
chunk: 0.000s-11.000s
speech spans: 4
  1. 0.220s-2.308s
  2. 3.260s-4.420s
  3. 5.308s-7.716s
  4. 8.092s-10.692s
```

Useful parameter pass:

```sh
uv run ja-media vad-local ../../examples/input/jfk.wav \
  --threshold 0.35 \
  --min-silence-s 0.10 \
  --speech-pad-s 0.03 \
  --format json
```

Options:

- `--start-s` / `--end-s`: run VAD on only part of the file.
- `--threshold`: speech probability threshold. Lower values usually produce
  more speech spans.
- `--min-speech-s`: discard shorter speech regions.
- `--min-silence-s`: silence needed before ending a speech region.
- `--speech-pad-s`: padding around detected speech.
- `--merge-gap-s`: merge close post-processed spans.
- `--channel`: use one channel instead of folding channels to mono.
- `--model-id`: defaults to `mlx-community/silero-vad`.
- `--dump-speech-dir`: write detected speech spans as FLAC files.
- `--split-every-minutes`: plan ASR chunks by targeting cuts every N minutes.
- `--split-radius-s`: VAD search radius around each periodic split target.

Dump detected speech spans for listening:

```sh
uv run ja-media vad-local ../../examples/input/jfk.wav \
  --dump-speech-dir ../../examples/output/jfk-vad-spans \
  --format text
```

Plan VAD-aware periodic chunks:

```sh
uv run ja-media vad-local ../../examples/input/jfk.wav \
  --split-every-minutes 0.05 \
  --split-radius-s 1.0 \
  --format text
```

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

## Library Example

See [../../docs/vad-library-example.md](../../docs/vad-library-example.md) for a
worked example using the library API directly.
