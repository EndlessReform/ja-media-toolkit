---
title: Voice Activity Detection (VAD)
description: Guide to using VAD for audio splitting and speech extraction.
---

## Core Use Cases

Within the `ja-media-toolkit`, VAD is primarily used for two tasks:

1.  **Planning ASR Chunks**: When splitting long audio files for Automatic Speech Recognition (ASR), we target a desired split point (e.g., every 10 minutes) and then "nudge" that point to the nearest non-speech boundary. This ensures that the ASR model receives clean, complete utterances.
2.  **Speech Extraction**: Extracting only the regions containing speech for benchmarks, listening checks, or utterance-level analysis.

---

## Quick Start: Command Line

The quickest way to use VAD is via the `vad-local` tool. These examples assume you are working within the `envs/apple` environment.

### 1. Plan ASR Splits
To split a long file into roughly 10-minute chunks without cutting mid-sentence:

```bash
cd envs/apple
uv sync
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --split-every-minutes 10 \
  --split-radius-s 60 \
  --format text
```

### 2. Dump Speech to Files
If you want to export the detected speech spans as separate audio files for verification:

```bash
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --dump-speech-dir ../../examples/output/vad-spans \
  --format text
```

### 3. Tuning & Inspection
You can use `vad-local` to tune parameters and inspect how the VAD model is behaving on your specific audio:

```bash
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --threshold 0.35 \
  --min-silence-s 0.10 \
  --speech-pad-s 0.03 \
  --format json
```

**Pro Tip:** The default output format is **WAV** on macOS because it integrates better with Finder's Quick Look. Use `--dump-audio-format flac` if you need smaller files and don't mind the loss of native Finder playback.

---

## Command Line Options

When using `vad-local`, the following options are available:

- `--start-s` / `--end-s`: run VAD on only part of the file.
- `--threshold`: speech probability threshold. Lower values usually produce more speech spans.
- `--min-speech-s`: discard shorter speech regions.
- `--min-silence-s`: silence needed before ending a speech region.
- `--speech-pad-s`: padding around detected speech.
- `--merge-gap-s`: merge close post-processed spans.
- `--channel`: use one channel instead of folding channels to mono.
- `--model-id`: defaults to `mlx-community/silero-vad`.
- `--dump-speech-dir`: write output chunks as audio files. In plain VAD mode, this writes detected speech spans; with `--split-every-minutes`, this writes the planned split chunks.
- `--dump-audio-format`: choose `wav` or `flac` for dumped chunks.
- `--split-every-minutes`: plan ASR chunks by targeting cuts every N minutes.
- `--split-radius-s`: VAD search radius around each periodic split target.

The segmentation options default to the global `[vad]` config when present.
Flags override only the value they name, so this is a good pattern for tuning:

```toml
[vad]
threshold = 0.25
min_speech_s = 0.10
min_silence_s = 0.08
speech_pad_s = 0.08
merge_gap_s = 0.10
```

Then run small one-off probes without changing the config:

```bash
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --threshold 0.20 \
  --format text
```

---

## Developer's Guide: Library Usage

If you are integrating VAD into a Python script, there are two primary workflows.

### Workflow A: Periodic ASR Splitting
Use this when you need to divide a long file into manageable pieces for ASR. The planner only analyzes small "search windows" around your target split points to remain efficient.

```python
from pathlib import Path
from ja_media_apple.vad import MlxAudioVadBackend
from ja_media_core.audio import full_audio_chunk, probe_audio_source, resolve_audio_source
from ja_media_core.vad import VadOptions, plan_vad_splits

repo_root = Path("../..").resolve()
source = resolve_audio_source(
    repo_root / "examples" / "input" / "example_走る高級レストランに乗ってきた.mp3",
    must_exist=True,
)
audio_format = probe_audio_source(source)
source_chunk = full_audio_chunk(source, audio_format, kind="source")

backend = MlxAudioVadBackend()
asr_chunks = plan_vad_splits(
    source_chunk,
    backend,
    every_s=30 * 60,
    search_radius_s=60.0,
    vad_options=VadOptions(
        threshold=0.5,
        min_speech_s=0.25,
        min_silence_s=0.20,
        speech_pad_s=0.05,
    ),
    kind="asr_chunk",
    metadata={"purpose": "asr"},
)
```
The resulting `asr_chunks` is a list of `AudioChunk` objects. Each contains metadata explaining why the cut happened (e.g., `"reason": "nearest qualifying silence"`).

### Workflow B: Extracting Every Speech Segment
Use this when you need to isolate every single utterance in a file.

```python
from ja_media_core.vad import VadOptions, speech_chunks_from_timeline

# Use the backend to detect all speech in a chunk
timeline = backend.detect(
    [source_chunk],
    options=VadOptions(
        threshold=0.5,
        min_speech_s=0.25,
        min_silence_s=0.20,
        speech_pad_s=0.05,
    ),
)[0]

# Convert that timeline into a list of AudioChunks
speech_chunks = speech_chunks_from_timeline(
    timeline,
    min_duration_s=0.25,
    kind="speech",
)
```

---

## Architecture & Core Concepts

To keep the system flexible, we decouple the **concept of a chunk** from the **actual audio data**.

### AudioChunk vs. InMemoryAudioChunk
- **`AudioChunk`**: A lightweight "pointer" or view. It contains coordinates (start time, end time) and a reference to the source file. It is cheap to pass around in JSON, manifests, or job queues.
- **`InMemoryAudioChunk`**: The "materialized" version. This contains the actual decoded PCM sample arrays.

**Why?** Memory management. A 60-minute stereo 48kHz file can take ~1.4 GB of RAM if fully decoded. By using `AudioChunk`, we only decode the specific segments we need.

### The Backend Protocol
The VAD logic is split into a core contract (`packages/core`) and specific implementations (`envs/*`). This allows us to swap the ML model or the runtime (e.g., moving from MLX on Apple to CUDA on Nvidia) without changing the business logic.

All backends must implement the `VadBackend` protocol:
```python
class VadBackend(Protocol):
    name: str

    def detect(
        self,
        chunks: Sequence[AudioChunk],
        *,
        options: VadOptions | None = None,
    ) -> list[VadTimeline]:
        ...
```

## Implementation Notes: Apple (MLX)

The `MlxAudioVadBackend` currently utilizes `mlx-community/silero-vad`. 

**Dependency Note:** This environment pins `mlx-audio` to a specific git commit. This is necessary because the Silero VAD implementation was added to the upstream repository after the last major PyPI release.
