# Apple Environment

This environment is for Mac-local workflows: lightweight audio utilities,
MLX-backed experiments, and client-local preprocessing before ASR.

Run commands from this directory. The root project is only for workspace
coordination and shared package editing.

```sh
cd envs/apple
uv sync
```

## Transcription

`transcribe` is the main Apple-local ASR entrypoint. It loads a local
VibeVoice audio encoder/projector bundle, uses VAD to plan source-aligned ASR
chunks when needed, and sends mixed Chat Completions `prompt_embeds` to the
configured vLLM server.

The local machine never loads the decoder weights. It does need this env's
audio-side dependencies and access to the configured checkpoint.

Create a local config at `envs/apple/config.local.toml`. This file is ignored
by git because it contains machine-local URLs and model paths:

```toml
[asr]
default_backend = "vibevoice_vllm_local"

[asr.backends.vibevoice_vllm_local]
type = "vibevoice_vllm"
vllm_base_url = "http://<host-url>:8000"
vllm_model = "/models/vibevoice"
checkpoint = "jkeisling/vibevoice-encoder-only"
device = "cpu"
dtype = "float32"
load_on_startup = true
vad_model_id = "mlx-community/silero-vad"
target_split_s = 300
split_search_radius_s = 45
prefer_split_before_target = false
rejoin_overlap_s = 0
timeout_s = 12000
max_concurrent_requests = 4
max_output_tokens = 2048
temperature = 0.0
top_p = 1.0
```

The split fields are active in `transcribe`: the command first plans ASR chunks
with VAD, then sends those chunks to the selected ASR backend. The example above
targets cuts about every five minutes, searches up to 45 seconds around each
target for a cleaner silence boundary, and rejoins transcript segments by
source-relative timestamps when the model returns parseable JSON.

Smoke-test config, imports, and model startup without calling vLLM:

```sh
uv run ja-media transcribe -c config.local.toml ../../examples/input/jfk.wav \
  --startup-only \
  --format text
```

Run ASR against the configured vLLM server:

```sh
uv run ja-media transcribe -c config.local.toml ../../examples/input/jfk.wav \
  --language en \
  --format text
```

For long batches, tune vLLM submit concurrency either in config with
`max_concurrent_requests` or per run with `--max-concurrent-requests`. The
backend keeps local audio encoding bounded and submits only that many decode
requests to vLLM at once:

```sh
uv run ja-media transcribe -c config.local.toml "../../examples/input/*.mp3" \
  --language ja \
  --max-concurrent-requests 8 \
  --srt-dir ../../examples/output/asr-srt \
  --format json
```

`transcribe` accepts one or more file paths or glob patterns. Quote glob
patterns so the command can expand and sort them consistently:

```sh
uv run ja-media transcribe -c config.local.toml "../../examples/input/*.wav" \
  --language en \
  --format json
```

Write one `.srt` file per input with `--srt-dir`. The command still keeps and
prints the JSON transcript representation internally; SRT is rendered from the
parsed source-relative segments when available, with a chunk-level fallback when
the model returns only plain text. Each SRT uses the same stem as its source
file, such as `episode.mp3` to `episode.srt`:

```sh
uv run ja-media transcribe -c config.local.toml "../../examples/input/*.mp3" \
  --language ja \
  --srt-dir ../../examples/output/asr-srt \
  --format text
```

For files longer than `target_split_s`, `transcribe` invokes the configured VAD
model before ASR:

```sh
uv run ja-media transcribe -c config.local.toml ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --language ja \
  --format text
```

The command also accepts `--start-s` and `--end-s`, which is useful when
checking one planned chunk or debugging rejoin boundaries.

## Inspecting VAD Chunks

`vad-local` is an Apple-only convenience command for trying VAD parameters on a
local file and inspecting the chunk plan that `transcribe` relies on before
ASR. It is intentionally not the final cross-environment CLI.

Use `vad-local` when you want to inspect or dump planned chunks without calling
ASR:

```sh
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --split-every-minutes 5 \
  --split-radius-s 45 \
  --dump-speech-dir ../../examples/output/kyushu-asr-5min \
  --dump-audio-format flac \
  --format text
```

The checked-in MP3 fixture probes as 1403.2399166666667 seconds (23:23.240),
48 kHz stereo MP3. Example 10-minute split output:

```text
source: /Users/ritsuko/projects/ai/audio/ja-media-toolkit/examples/input/example_走る高級レストランに乗ってきた.mp3
model: mlx-community/silero-vad
chunk: 0.000s-1403.240s
split chunks:
  0.000s-600.388s next_target=600.0 fallback=False reason=nearest qualifying silence
  600.388s-1200.068s next_target=1200.0 fallback=False reason=nearest qualifying silence
  1200.068s-1403.240s next_target=None fallback=None reason=None
```

Useful tuning pass:

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
