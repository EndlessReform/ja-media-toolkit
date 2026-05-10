# VAD Feature Docs

This page documents the current VAD implementation: file-level split planning,
speech-span extraction, local listening checks, and the dependency boundaries
that matter for future backends.

Run examples from the Apple environment:

```sh
cd envs/apple
```

## Plan Periodic ASR Chunks

This is the main library path for long-file splitting. The caller provides a
file, a target interval, and a VAD search radius. The planner returns
`AudioChunk`s over the original source.

```python
from pathlib import Path

from ja_media_apple.vad import MlxAudioVadBackend
from ja_media_core.audio import full_audio_chunk, probe_audio_source, resolve_audio_source
from ja_media_core.vad import VadOptions, plan_vad_splits

repo_root = Path("../..").resolve()
source = resolve_audio_source(
    repo_root / "examples" / "input" / "jfk.wav",
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

for chunk in asr_chunks:
    print(
        f"{chunk.start_s:.3f}s-{chunk.end_s:.3f}s "
        f"frames={chunk.source_start_frame}-{chunk.source_end_frame}"
    )
```

For a short local smoke test, use a smaller interval:

```sh
uv run ja-media vad-local ../../examples/input/jfk.wav \
  --split-every-minutes 0.05 \
  --split-radius-s 1.0 \
  --format text
```

## Detect Speech Spans

For utterance-like chunks, call the backend and convert the timeline to
`AudioChunk`s:

```python
from ja_media_core.vad import speech_chunks_from_timeline

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

`timeline.speech` is available for inspection:

```python
for span in timeline.speech:
    print(f"{span.start_s:.3f}s-{span.end_s:.3f}s")
```

## Listen To Detected Spans

The Apple CLI can dump detected speech chunks as FLAC files:

```sh
uv run ja-media vad-local ../../examples/input/jfk.wav \
  --dump-speech-dir ../../examples/output/jfk-vad-spans \
  --format text
```

## Return Shape

The primary artifact for downstream ASR is `list[AudioChunk]`. Each chunk keeps
source coordinates and metadata:

```python
AudioChunk(
    source=...,
    start_s=...,
    end_s=...,
    source_start_frame=...,
    source_end_frame=...,
    kind="asr_chunk",
    metadata={
        "boundary_source": "vad",
        "next_cut_fallback": False,
        ...
    },
)
```

`VadTimeline` is an intermediate result used by backends and planners. It can be
logged or inspected, but callers that only need chunks do not need to keep it.

## Core Contracts

Core VAD code lives in `packages/core` and does not import ML runtimes. Backends
live in runnable environments such as `envs/apple` and implement:

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

The main shared types are:

- `AudioSource`: identifies local files and later remote sources such as `s3://`.
- `AudioFormat`: sample rate, channel count, duration, frame count, codec, and
  container metadata.
- `AudioChunk`: a lightweight source-coordinate view. It does not own sample
  arrays.
- `InMemoryAudioChunk`: decoded samples plus the source chunk and format
  metadata.
- `VadTimeline`: backend output for one inspected chunk.
- `SpeechSpan`: speech interval in source-media seconds.

For file splitting, prefer `plan_vad_splits(...)`. It generates bounded search
windows around each target cut, asks the backend for VAD only in those windows,
and returns planned `AudioChunk`s.

For speech extraction, use `speech_chunks_from_timeline(...)` or the backend
convenience method `detect_speech_chunks(...)`.

## Audio Materialization

`AudioChunk` stays cheap enough for manifests, ASR job queues, and JSON output.
Decoded arrays live in `InMemoryAudioChunk`.

Current helpers:

```python
materialized = materialize_audio_chunk(chunk)
write_audio_chunk(chunk, "span.flac", format="FLAC")
```

The current local decoder is `soundfile`. It supports explicit frame-range reads
and preserves sample rate/channel information during probe and decode. Add an
ffmpeg-backed path later for common container formats that `soundfile` cannot
decode directly.

Approximate float32 PCM sizes:

- 2 minutes, mono 16 kHz: about 8 MB.
- 30 minutes, mono 16 kHz: about 115 MB.
- 60 minutes, stereo 48 kHz: about 1.4 GB.

VAD split planning avoids materializing the whole file by decoding only the
search windows passed to the backend.

## Current Backend

`MlxAudioVadBackend` defaults to `mlx-community/silero-vad`.

The Apple environment currently pins `mlx-audio` to upstream git commit
`f7c11556eda88731be5cc75ddbdf4a4cb9eeaafc`. PyPI `mlx-audio==0.4.3` was
released before upstream added `mlx_audio.vad.models.silero_vad`, so the git pin
is needed for the model-card API until a later PyPI release includes it.
