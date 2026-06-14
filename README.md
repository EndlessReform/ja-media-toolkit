# ja-media-toolkit

Tools for organizing and processing Japanese media as a language learner:
audio ingest, VAD, ASR chunk planning, transcript alignment, and related local
workflows.

## Where To Start

- [Monorepo philosophy](docs/monorepo-philosophy.md): package/env layout.
- [uv tool installs](docs/uv-tool-install-frontends.md): installing the shared
  `ja-media` frontend as a persistent command with an environment extra.
- [VAD](docs/vad.md): library usage, split planning, and local smoke tests.
- [Apple environment](envs/apple/README.md): MLX-specific developer notes and
  dependency pins.

## Persistent CLI Install

For normal development, run the command from the package that owns the work:

- `cd packages/frontend && uv run ja-media subsync tui ...` for the lightweight
  subtitle TUI and other frontend-owned tools.
- `cd envs/apple && uv run ja-media transcribe ...` for Apple-local
  transcription, VAD, and other MLX/audio runtime work.

The repository root coordinates shared packages and docs; it is not the normal
place to run the `ja-media` console script.

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

If `ja-media` is already installed and you want to replace it with another ref,
add `--force` to the `uv tool install` command.

## Local Checkout Usage

When working from a checkout, run frontend-owned commands through
`packages/frontend`:

```sh
cd packages/frontend
uv sync
uv run ja-media subsync tui --help
```

Run Apple runtime commands through `envs/apple`:

```sh
cd envs/apple
uv sync
uv run ja-media transcribe --help
```

The root project coordinates shared packages. The frontend command surface lives
under `packages/frontend`; runnable Apple dependencies live under `envs/apple`.

## Transcription

`transcribe` is the main Apple-local ASR entrypoint. It loads a local VibeVoice
audio encoder/projector bundle, uses VAD to plan source-aligned ASR chunks when
needed, and sends mixed Chat Completions `prompt_embeds` to the configured vLLM
server.

The local machine never loads the decoder weights. It does need the Apple env's
audio-side dependencies and access to the configured checkpoint.

Create a local config at `envs/apple/config.local.toml`. This file is ignored by
git because it contains machine-local URLs and model paths:

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
`max_concurrent_requests` or per run with `--max-concurrent-requests`:

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
the model returns only plain text:

```sh
uv run ja-media transcribe -c config.local.toml "../../examples/input/*.mp3" \
  --language ja \
  --srt-dir ../../examples/output/asr-srt \
  --format text
```

The command also accepts `--start-s` and `--end-s`, which is useful when checking
one planned chunk or debugging rejoin boundaries.

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

## Inspecting VAD Chunks

`vad-local` is the current Apple-backed convenience command for trying VAD
parameters on a local file and inspecting the chunk plan that `transcribe`
relies on before ASR.

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
