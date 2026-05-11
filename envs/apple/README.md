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
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --split-every-minutes 10 \
  --split-radius-s 60 \
  --format text
```

The checked-in MP3 fixture probes as 1403.2399166666667 seconds
(23:23.240), 48 kHz stereo MP3. Example 10-minute split output:

```text
source: /Users/ritsuko/projects/ai/audio/ja-media-toolkit/examples/input/example_走る高級レストランに乗ってきた.mp3
model: mlx-community/silero-vad
chunk: 0.000s-1403.240s
split chunks:
  0.000s-600.388s next_target=600.0 fallback=False reason=nearest qualifying silence
  600.388s-1200.068s next_target=1200.0 fallback=False reason=nearest qualifying silence
  1200.068s-1403.240s next_target=None fallback=None reason=None
```

Useful parameter pass:

```sh
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
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
- `--dump-speech-dir`: write output chunks as audio files. In plain VAD mode,
  this writes detected speech spans; with `--split-every-minutes`, this writes
  the planned split chunks.
- `--dump-audio-format`: choose `wav` or `flac` for dumped chunks. WAV is the
  default because macOS Preview/Quick Look reports its playback timeline more
  reliably.
- `--split-every-minutes`: plan ASR chunks by targeting cuts every N minutes.
- `--split-radius-s`: VAD search radius around each periodic split target.

Dump detected speech spans for listening:

```sh
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --dump-speech-dir ../../examples/output/vad-spans \
  --format text
```

Plan VAD-aware periodic chunks:

```sh
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --split-every-minutes 10 \
  --split-radius-s 60 \
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

## Library Docs

See [../../docs/vad.md](../../docs/vad.md) for the VAD library API and pipeline
usage.
