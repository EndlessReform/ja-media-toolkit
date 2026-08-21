# Qwen3 Backend Implementation Notes

This note expands the Qwen3-specific backend concerns from
`forced-aligner-backend-design.md`. The short version: MLX and vLLM can share
span planning, prompt construction, timestamp validation, cue projection, and
evaluation, but they cannot share audio execution or timestamp-row extraction.

## MLX Usability

The MLX implementation is useful, but not cleanly shaped for our desired API
yet. It currently bundles:

- tokenization in `ForceAlignProcessor`;
- prompt construction with `<timestamp><timestamp>`;
- audio feature extraction and audio-token expansion;
- model execution;
- monotonic timestamp repair with `fix_timestamp`;
- a serial loop over samples that clears cache between samples.

Throwing out their prompting is feasible, not heroic. The important fact is that
Qwen3's forced-aligner contract is prompt based. Their `generate()` builds
`aligner_input_text`, replaces `<|audio_pad|>` with the computed audio-token
count, tokenizes, runs the model, then reads predictions at timestamp-token
positions. A wrapper can duplicate or lightly fork that middle section so the
input text comes from toolkit `AlignmentWindow.spans` instead of
`ForceAlignProcessor.encode_timestamp()`.

What can be shared with vLLM:

- span planning;
- prompt suffix construction from spans;
- timestamp-pair validation;
- monotonic repair policy, if we trust it;
- projection from span timings back to cues;
- evaluation and retimed SRT output.

What cannot be shared directly:

- audio feature extraction;
- audio-token expansion mechanics;
- model invocation;
- timestamp-row extraction details. MLX can mask local `input_ids`; vLLM needs
  right-aligned extraction because server-side audio expansion changes row
  positions.

Batching is the big limitation. The installed MLX path accepts lists but loops
over them one by one. Real batching would require padding variable-length audio
features and prompt token sequences, keeping per-sample timestamp positions, and
being careful with MLX memory pressure. That is probably not first-slice work.
The backend contract should still accept many windows; the MLX implementation
can report `supports_batch=False` or `effective_batch_size=1` and process
serially.

## Where The vLLM Client Lives

The vLLM server does not need to know about ja-media. It should be as close to
stock vLLM as possible: model name, pooling runner, raw chat template,
architecture override, pinned image/version, cache mounts, and health checks.

The project-specific client is a different thing. It knows how to turn
`AlignmentWindow` into a Qwen3 prompt, encode or serve the audio window, call
`/pooling`, decode logits, and return `AlignmentWindowResult`. That code should
not live in `packages/core` because it imports model/runtime dependencies like
`transformers` and understands Qwen/vLLM-specific response semantics. It also
should not live in `envs/apple`, because it is not Apple-specific.

Recommended placement: create a small model-adapter package, for example
`packages/qwen3-aligner` or `envs/qwen3-aligner` depending on whether we want it
in the root workspace immediately. It would provide:

- strict config model for `type = "qwen3_vllm_pooling"`;
- `Qwen3VllmForcedAlignerBackend`;
- prompt construction and timestamp extraction helpers;
- tests with fake logits;
- server startup assets such as `raw_content_chat_template.jinja`;
- optional Docker wrapper commands for running stock/pinned vLLM.

The frontend proof-of-value CLI can depend on this package through an optional
extra, just as frontend currently has an `[apple]` extra. That gives us an
install shape like:

```sh
cd packages/frontend
uv run --isolated --with-editable '.[qwen3-aligner]' ja-media forced-align eval ...
```

## Compute Plane Startup

The compute plane should not need `uv`, this repo, or any ja-media Python
package to run vLLM. The server side should be a plain, portable artifact:

- a pinned vLLM image or Dockerfile;
- a raw chat template file;
- a `docker run` example and, if repetition warrants it, a small Compose file;
- explicit environment variables for cache paths, model name, port, and GPU
  selection.

That is the lowest-friction shape for a rented GPU box: copy a directory, run
Docker, expose `/pooling`. It also avoids pretending that starting vLLM is a
domain operation. vLLM startup is infrastructure, not toolkit logic.

The project-specific Python belongs on the caller/control side, not the GPU
server. It is justified only where it manipulates project concepts:

- building `AlignmentWindow` values from cue/audio artifacts;
- constructing Qwen3 prompts from toolkit spans;
- materializing audio windows or data URLs;
- decoding `/pooling` logits back into `SpanAlignment` values;
- writing run manifests and evaluation artifacts.

A Python `serve-vllm` wrapper is therefore optional, not the default
architecture. It may become useful on the development machine if we repeatedly
mis-type long Docker commands or need local health checks, but it should emit
plain Docker/Compose instructions and should not be required on cloud compute.

Concrete failures this avoids:

- a cloud GPU worker failing because the repo was not checked out or `uv sync`
  was not run;
- startup scripts becoming coupled to workspace paths such as
  `.ja-media-runs`;
- server images needing rebuilds when only client-side prompt/evaluation code
  changes;
- losing the option to use managed vLLM endpoints or commodity containers.

So the default proof-of-value should be: static server assets for vLLM, Python
client adapter for Qwen3 `/pooling`, and no ja-media code on the compute plane.

## Shared Qwen3 Helper Layer

Both MLX and vLLM should use one shared Qwen3 helper layer for prompt-adjacent
logic:

- normalize toolkit spans into a Qwen3 prompt suffix;
- count expected timestamp markers;
- pair predicted start/end timestamps;
- apply or skip monotonic repair with an explicit policy name;
- expose backend metadata such as timestamp resolution and model revision.

The helper should not load MLX, start vLLM, or decode audio. Those are execution
concerns owned by concrete backends.
