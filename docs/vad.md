# VAD

VAD support is currently used for two pipeline tasks:

- plan ASR chunks by targeting regular split points and moving each split to a
  nearby non-speech boundary;
- extract speech-only chunks for listening checks, benchmarks, or later
  utterance-level processing.

Most callers should consume `AudioChunk` outputs. `VadTimeline` is available for
debugging and parameter tuning, but it is not the main downstream artifact.

## Quick Start

Run from the Apple environment:

```sh
cd envs/apple
uv sync
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --split-every-minutes 10 \
  --split-radius-s 60 \
  --format text
```

The default example fixture is
`examples/input/example_走る高級レストランに乗ってきた.mp3`. It probes as
1403.2399166666667 seconds (23:23.240), 48 kHz stereo MP3.

Dump detected spans as audio files for listening:

```sh
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --dump-speech-dir ../../examples/output/vad-spans \
  --format text
```

The local dump default is WAV because macOS Preview/Quick Look reports WAV
timelines more reliably than FLAC. Use `--dump-audio-format flac` when smaller
files matter more than Finder playback behavior.

When `--dump-speech-dir` is combined with `--split-every-minutes`, it writes the
planned split chunks instead of every detected speech span. For the example file
above, the 10-minute split command writes three audio files. Dumped filenames
include both the source timeline range and the output duration, for example
`src_000600388ms-001200068ms_dur_000599680ms.wav`.

Plan periodic chunks using VAD search windows:

```sh
uv run ja-media vad-local ../../examples/input/example_走る高級レストランに乗ってきた.mp3 \
  --split-every-minutes 10 \
  --split-radius-s 60 \
  --format text
```

Current smoke-test output for 10-minute targets:

```text
chunk: 0.000s-1403.240s
split chunks:
  0.000s-600.388s next_target=600.0 fallback=False reason=nearest qualifying silence
  600.388s-1200.068s next_target=1200.0 fallback=False reason=nearest qualifying silence
  1200.068s-1403.240s next_target=None fallback=None reason=None
```

## Library: Periodic ASR Chunks

Use this path when splitting long files for ASR. The planner only materializes
bounded VAD search windows around each target split.

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

`asr_chunks` is a `list[AudioChunk]` over the original source. Cut details are
recorded in chunk metadata, for example:

```python
{
    "boundary_source": "vad",
    "next_target_s": 1800.0,
    "next_cut_s": 1798.42,
    "next_cut_fallback": False,
    "next_cut_reason": "nearest qualifying silence",
}
```

## Library: Speech Chunks

Use this path when the desired output is speech regions rather than regular ASR
windows.

```python
from ja_media_core.vad import VadOptions, speech_chunks_from_timeline

timeline = backend.detect(
    [source_chunk],
    options=VadOptions(
        threshold=0.5,
        min_speech_s=0.25,
        min_silence_s=0.20,
        speech_pad_s=0.05,
    ),
)[0]

speech_chunks = speech_chunks_from_timeline(
    timeline,
    min_duration_s=0.25,
    kind="speech",
)
```

Inspect `timeline.speech` when tuning parameters:

```python
for span in timeline.speech:
    print(f"{span.start_s:.3f}s-{span.end_s:.3f}s")
```

## Core Contracts

Core VAD code lives in `packages/core` and does not import model runtimes.
Backends live in runnable environments such as `envs/apple`.

Key types:

- `AudioSource`: source identifier and locator, currently local files and `s3://`
  references.
- `AudioFormat`: sample rate, channel count, duration, frame count, codec, and
  container metadata.
- `AudioChunk`: lightweight source-coordinate view. It does not own sample
  arrays.
- `InMemoryAudioChunk`: decoded samples plus the source chunk and format
  metadata.
- `VadTimeline`: backend output for one inspected chunk.
- `SpeechSpan`: speech interval in source-media seconds.

Backend protocol:

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

Main helpers:

- `plan_vad_splits(...) -> list[AudioChunk]`
- `speech_chunks_from_timeline(...) -> list[AudioChunk]`
- `materialize_audio_chunk(...) -> InMemoryAudioChunk`
- `write_audio_chunk(..., format="WAV") -> Path`

## Materialization

`AudioChunk` is cheap enough for manifests, ASR job queues, and JSON output.
Decoded arrays live in `InMemoryAudioChunk`.

Current local decoding uses `soundfile`, with explicit frame-range reads. Add an
ffmpeg-backed path later for common media formats that `soundfile` cannot decode
directly.

Approximate float32 PCM sizes:

- 2 minutes, mono 16 kHz: about 8 MB.
- 30 minutes, mono 16 kHz: about 115 MB.
- 60 minutes, stereo 48 kHz: about 1.4 GB.

## Apple Backend

`MlxAudioVadBackend` defaults to `mlx-community/silero-vad`.

The Apple environment currently pins `mlx-audio` to upstream git commit
`f7c11556eda88731be5cc75ddbdf4a4cb9eeaafc`. PyPI `mlx-audio==0.4.3` was
released before upstream added `mlx_audio.vad.models.silero_vad`, so the git pin
is needed for the model-card API until a later PyPI release includes it.
