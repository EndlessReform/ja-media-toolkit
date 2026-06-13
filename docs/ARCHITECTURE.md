# Architecture

This repo is organized around one rule: data contracts should outlive the
models and runtimes that satisfy them. Model choices, inference servers, local
accelerators, and deployment locations are expected to change. Core request and
result objects should stay stable enough to support manifests, queues, and
batch workflows across those changes.

## Package Boundaries

`packages/core` owns runtime-free contracts and shared configuration patterns.
It may depend on lightweight schema libraries such as Pydantic, but it must not
import model runtimes, accelerator libraries, or environment-specific clients.

`envs/*` packages own runnable implementations. They can depend on MLX, CUDA
libraries, HTTP clients, model checkpoints, or machine-local services because
they are selected explicitly by the user and installed in their own uv
environment.

This mirrors the existing VAD split:

- core describes audio sources, chunks, VAD contracts, ASR contracts, and config
  envelopes;
- environment packages implement concrete VAD and ASR backends;
- CLIs compose those contracts into workflows.

## ASR Contracts

ASR core contracts live in `packages/core/src/ja_media_core/asr.py`.

`AsrRequest` is the durable data-transfer object. It describes what should be
transcribed: source-coordinate `AudioChunk` values, language, task, contextual
hints, hotwords, and whether timestamps or diarization are desired. It should
not grow backend sampling controls such as `temperature`, `top_p`, beam size, or
token limits. Those controls are runtime policy, not user intent.

`AsrRuntimeOptions` carries invocation-time controls. Generic fields such as a
timeout can be shared, while backend-specific controls live in
`backend_options`. This keeps a vLLM-oriented option like `max_output_tokens`
from leaking into every future backend.

`AsrTranscript` and `AsrSegment` are normalized results. Segment timestamps are
source-relative, not chunk-local, so downstream renderers can rejoin chunks,
write SRT, compare against subtitles, or index transcripts without knowing how
the audio was split for inference.

`AsrBackend` is the synchronous compatibility protocol. `AsyncAsrBackend` is
preferred for remote or decode-heavy backends because callers can keep multiple
chunk requests in flight. `AsrJob` and `AsrJobRecord` are the queue-friendly
wrapper shape for future service/process boundaries.

## Pipeline Shape

VAD is a pipeline stage before ASR. ASR backends should receive already-planned
`AudioChunk` values and should not own silence detection, long-file splitting,
or source selection policy.

The Apple `transcribe` command currently follows this shape:

1. Load ASR config and resolve input paths or globs.
2. Probe each media file and select the requested source range.
3. Use VAD to plan ASR chunks when the file exceeds the configured target split
   duration.
4. Instantiate the selected ASR backend.
5. Submit one combined ASR request so concurrency limits apply across the whole
   batch.
6. Keep JSON-like transcript data internally, then optionally render sidecar
   formats such as SRT.

The split/rejoin parameters belong to the workflow config because they describe
how the CLI prepares work for ASR. They are not intrinsic to the VibeVoice model
or to the core ASR request.

## Configuration

Shared config machinery lives in `packages/core/src/ja_media_core/config.py`.

The top-level config is TOML. Discovery uses an explicit `-c/--config` path,
`JA_MEDIA_CONFIG`, or the XDG default at
`~/.config/ja-media-toolkit/config.toml`. Core parses backend entries into broad
Pydantic models without importing environment-specific packages.

Backend selection follows this pattern:

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
target_split_s = 300
split_search_radius_s = 45
max_concurrent_requests = 4
max_output_tokens = 2048
temperature = 0.0
top_p = 1.0
repetition_penalty = 1.1
vllm_request_max_attempts = 3
vllm_request_retry_backoff_s = 1.0
```

Core provides the registry shape: a base backend config model, concrete
subclasses selected by `type`, a named backend dictionary, and a selected active
backend. Environment packages register or parse only the backend types they can
actually instantiate.

Heavy imports should happen no earlier than the selected backend boundary. It is
fine for an environment package to import its concrete backend once the config
has selected that backend. Additional layers of import indirection after that
make smoke tests less valuable because they can avoid the actual model load.

## Apple VibeVoice vLLM Backend

The Apple VibeVoice backend lives in
`envs/apple/src/ja_media_apple/asr_vibevoice_vllm.py`. It keeps VibeVoice's
audio-side work local and sends prompt embeddings to a remote OpenAI-compatible
vLLM server.

The local side:

- materializes `AudioChunk` values;
- decodes audio with `librosa` so WAV, MP3, and FLAC inputs work;
- loads the VibeVoice audio encoder, semantic tokenizer encoder, and multimodal
  projector;
- serializes mixed Chat Completions content containing text plus
  `prompt_embeds`.

The remote vLLM side owns decoder-heavy generation. This is the point of using
vLLM for long transcription batches: decode dominates, and many chunks from
many hours of audio can be submitted concurrently.

The backend uses async HTTP submission with a configurable
`max_concurrent_requests`. Local audio encoding remains bounded, while remote
decode requests can overlap. Transient vLLM transport failures are retried with
`vllm_request_max_attempts` and `vllm_request_retry_backoff_s`; client-side
payload errors are not retried. Progress is shown over completed chunks, and the
ordered result list is restored before returning transcripts.

Response parsing is intentionally tolerant. When the model returns parseable
JSON segment objects, the backend converts them into source-relative
`AsrSegment` values. When the response is plain or truncated text, the raw text
and vLLM usage metadata are still preserved in the transcript metadata.

## Documentation Roles

Architecture docs describe durable boundaries and decisions. Environment
READMEs describe concrete user commands and machine-local setup. Files under
`docs/plans/` are temporary design notes and should be deleted or replaced once
their decisions have landed.
