# Forced Aligner Backend Design

## Problem Frame

The immediate goal is proof of value: take cleaned SRT text, align it against
episode audio, and produce enough evidence to decide whether forced alignment is
better than ffsubsync-style anchoring for the messy subtitle candidates we have.
This is not yet the final subtitle-retiming product.

The stable existing cue contract is `SubtitleCue` in
`packages/core/src/ja_media_core/transcripts.py`. Keep that small. A cue is
source-clock text plus source-clock timing. Forced alignment should live beside
it: consume cues and audio windows, emit timing evidence, then let downstream
policy decide how to retime, flag, compare, or discard.

Related notes:

- `srt-cleaning-batch-design.md`: current cleaned-SRT production pipeline.
- `qwen3-forced-aligner-pool-notes.md`: vLLM `/pooling` mechanics for
  `Qwen/Qwen3-ForcedAligner-0.6B`.
- `qwen3-backend-implementation-notes.md`: MLX usability, vLLM client
  placement, and compute-plane startup tradeoffs.
- `run-artifacts-and-serialization.md`: durable record boundaries and local
  workspace/S3 implications.

## Backend Creation And Config

Use the existing backend-group pattern from `ja_media_core.config`: a named
backend map plus a selected default. Core should grow a backend-neutral
`forced_alignment` config envelope, but not model-specific fields.

Example shape:

```toml
[forced_alignment]
default_backend = "qwen3_mlx_local"

[forced_alignment.backends.qwen3_mlx_local]
type = "qwen3_mlx"
model = "Qwen/Qwen3-ForcedAligner-0.6B"
device = "mps"
language = "Japanese"
max_window_s = 30

[forced_alignment.backends.qwen3_vllm_gpu]
type = "qwen3_vllm_pooling"
base_url = "http://gpu-box:8000"
model = "Qwen/Qwen3-ForcedAligner-0.6B"
timeout_s = 300
max_concurrent_requests = 4
audio_transport = "data_url"
```

Core knows only that a backend entry has a `type`. Concrete packages re-parse
the selected entry into strict Pydantic models, exactly like `envs/apple` does
for ASR today. That means:

- `packages/core` owns durable DTOs, protocols, and the generic config envelope.
- `envs/apple` owns `qwen3_mlx` config and factory because it imports MLX.
- A Qwen/vLLM adapter package owns `qwen3_vllm_pooling` config and factory
  because it imports `transformers` and understands Qwen timestamp extraction.
- The frontend/workflow code asks the active runtime registry to build the
  selected backend; it should not switch on backend names itself.

This keeps the config permanent while the implementation packages remain
replaceable. It also lets a single workflow run either local MLX or remote vLLM
without teaching core how either backend works.

## Core Contracts

Add a small `ja_media_core.forced_alignment` module rather than expanding
`transcripts.py`.

```python
@dataclass(frozen=True)
class AlignmentSpan:
    id: str
    cue_index: int
    text: str
    text_start: int | None = None
    text_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlignmentWindow:
    id: str
    audio: AudioChunk
    cues: tuple[SubtitleCue, ...]
    spans: tuple[AlignmentSpan, ...]
    language: str = "ja"
    span_policy: str = "qwen3-ja-v1"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpanAlignment:
    span_id: str
    start_s: float
    end_s: float
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlignmentWindowResult:
    window_id: str
    alignments: tuple[SpanAlignment, ...]
    backend: str
    coordinate_origin: Literal["window", "source"]
    metadata: dict[str, Any] = field(default_factory=dict)
```

The backend protocol should accept a sequence, not one window, because even the
simple local path should not force callers into one-at-a-time thinking:

```python
class ForcedAlignerBackend(Protocol):
    name: str

    def align(
        self,
        windows: Sequence[AlignmentWindow],
        *,
        runtime_options: ForcedAlignmentRuntimeOptions | None = None,
    ) -> list[AlignmentWindowResult]:
        ...
```

Backends may internally process serially. The contract should still be batch
shaped so the caller can preserve ordering, submit parallel HTTP requests, or
later exploit real backend batching without redesigning the workflow.

## What "Segment Cue Text" Means

Segmentation means choosing the text units that receive timestamps. For Qwen3,
the application writes:

```text
span1<timestamp><timestamp>span2<timestamp><timestamp>
```

so our chosen spans directly define the alignment units. For Japanese, a span
could be a nagisa token, MeCab token, kana/kanji character, short phrase, or a
hybrid. This choice is not cosmetic: it affects timestamp stability, cue
projection, and whether a weird token can be detected as "not real" by its
timing.

But not every forced aligner accepts arbitrary application-defined spans:

- Qwen3 forced aligner: yes, within reason. The caller inserts timestamp tokens,
  so toolkit-owned spans are a real backend input.
- MLX Qwen3 implementation: yes in principle, because it uses the same prompt
  mechanism; its current public `generate()` path happens to own tokenization.
- WhisperX-style aligners: partly. They usually align transcript text through a
  CTC/wav2vec alignment model and return model/dictionary word timings. We can
  pass transcript segments, but we should expect backend-owned word boundaries.
- MFA-style aligners: mostly no. MFA wants a lexicon/pronunciation model and
  produces word/phone intervals from that linguistic pipeline. For Japanese, the
  tokenizer/dictionary layer is part of the backend setup, not a tiny prompt
  choice.

So core should not pretend `AlignmentSpan` means "all backends must honor this
exact word definition." Instead, add backend capabilities:

```python
@dataclass(frozen=True)
class ForcedAlignerCapabilities:
    span_control: Literal["application", "backend", "lexicon"]
    supports_batch: bool
    supports_async: bool
    timing_resolution_s: float | None = None
```

The proof-of-value path can target Qwen3 with `span_control="application"`.
If we later add WhisperX or MFA, their adapters can return alignments with
backend-generated span IDs plus metadata mapping them back to cues.

## Qwen3 Backend Notes

The first proof-of-value backend family should be Qwen3 because it can honor
application-defined spans. The shared Qwen3 layer should own prompt suffix
construction, timestamp-pair validation, optional monotonic repair, and
projection helpers. Concrete backends own execution:

- MLX lives in `envs/apple`, can be a fast local smoke path, and will likely
  process windows serially at first.
- vLLM should be a CPU-side Qwen adapter client plus a mostly stock vLLM server.
  The client should not live in `packages/core`; it should live in a small
  model-adapter package with its own `transformers` dependency. The GPU server
  should not need `uv`, this repo, or ja-media imports.

See `qwen3-backend-implementation-notes.md` for the detailed reasoning on MLX
prompt replacement, batching limits, vLLM client placement, and why static
Docker/Compose-style server assets should be the default compute-plane shape.

## Pipeline Shape

1. Select episode audio and one cleaned SRT.
2. Parse cleaned SRT into `SubtitleCue` values.
3. Build source-clock alignment windows from cues plus configurable padding.
4. Create spans according to a named span policy.
5. Submit windows to a backend in batch-shaped calls.
6. Project span predictions back to cue timing using robust rules.
7. Evaluate original cue timing against predicted timing:
   - per-cue start/end residuals;
   - missing, non-monotonic, or impossible spans;
   - best global additive offset;
   - best affine transform, `aligned_time = source_time * scale + offset`;
   - residuals after additive and affine correction.
8. Emit review artifacts, suspicious-span logs, and retimed SRT candidates.

## Invocation Mode And Run Mode

The backend API should support direct invocation without a durable run. That is
the right shape for ranking a handful of SRT candidates or making a one-off
word-level ASS file.

For corpus retiming, timing evaluation, and replayable experiments, wrap the
same request/result DTOs in a run manifest. Core should know the portable
records; frontend/workflow code owns the storage layout.

One local run-mode layout could use a sibling workspace under the existing
`.ja-media-runs` convention:

```text
.ja-media-runs/
└── forced-align/
    └── anilist-101573/
        └── current/
            ├── run-manifest.json
            ├── windows.jsonl
            ├── backend-results.jsonl
            ├── cue-projections.jsonl
            ├── evaluation.json
            ├── suspicious-spans.jsonl
            └── retimed/
                ├── additive.srt
                ├── affine.srt
                └── direct-cue-projection.srt
```

`windows.jsonl` is the backend-neutral request log for this storage
implementation. It should contain audio window coordinates, cue IDs, span IDs,
text, span policy, and backend capability expectations. Backend-specific
payloads can go in `backend-results.jsonl` metadata for debugging.

See `run-artifacts-and-serialization.md` for the distinction between portable
schemas and workspace details.

## First Proof-Of-Value Slice

The smallest useful slice is:

```text
cleaned SRT + local audio
  -> windows.jsonl
  -> MLX or vLLM backend-results.jsonl
  -> cue-projections.jsonl
  -> evaluation.json + retimed SRT candidates
```

Recommended implementation order:

1. Add core DTOs, config envelope, capability model, and serialization tests.
2. Add span planning and window-building in frontend/workflow code.
3. Add the MLX backend as the fastest local smoke test, accepting serial
   execution.
4. Add the Qwen/vLLM adapter package with fake-logit tests before touching a
   real GPU server.
5. Add static vLLM server assets: raw chat template, pinned Docker invocation,
   and optional Compose file if repeated local startup needs it.
6. Compare direct cue projection, additive retiming, affine retiming, and
   ffsubsync output on the same episode.

## Open Questions

- Which Japanese span policy gives the most stable cue projection?
- Should windows be cue-clock based first, or VAD-anchored before alignment?
- Should Qwen3 timestamp monotonic repair happen in the backend adapter, or in
  shared postprocessing so MLX and vLLM stay comparable?
- Is vLLM data-URL audio acceptable for realistic windows, or should the client
  provide a tiny local file server?
- Does this deserve a root-workspace package immediately, or should the Qwen3
  adapter start as an environment package until the API settles?
