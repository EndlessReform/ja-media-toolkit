# Forced Alignment Run Artifacts And Serialization

## Layer Boundary

Serialization has two different meanings here:

- portable records: typed request/result/evaluation data that can be written to
  JSONL, queued, replayed, or stored somewhere other than the local filesystem;
- workspace layout: today's directory names, filenames, symlinks, caches, and
  "current" pointer under `.ja-media-runs`.

Core should know the portable records. Core should not know the workspace
layout. The local workspace is a frontend/workflow implementation detail, just
like a later S3 object layout would be an implementation detail.

This means `AlignmentWindow`, `AlignmentWindowResult`, cue projections, and
evaluation summaries can live in `ja_media_core.forced_alignment`. A
`.ja-media-runs/forced-align/anilist-.../current/` directory convention should
live in frontend/workflow code.

## Use Cases

| Use case | Durable run needed? | Primary output |
| --- | --- | --- |
| Pick the best existing SRT candidate for one series | Usually no | ranked candidate report, optional constant offset |
| Generate word-level ASS subtitles for mpv | Sometimes | word-timed subtitle artifact, optional rebuild manifest |
| Total recaptioning with authoritative timings | Yes | replayable manifest, results, cue projections, retimed subtitles |
| Evaluate auto-generated SRT timing for future models | Yes | reproducible metrics and backend evidence |
| Add timing to untimed ASR text | Depends | timed transcript/subtitles, optional manifest |

So the system needs two modes:

- invocation mode: call a backend on a few windows/candidates and return normal
  Python objects plus an optional report;
- run mode: persist a named set of inputs, backend metadata, outputs, and
  evaluation artifacts for replay.

The backend API should not care which mode is active. Persistence wraps the
same request/result objects.

## Core Portable Records

Core should define the durable data model, with schema versions on serialized
forms:

```python
@dataclass(frozen=True)
class AlignmentArtifactRef:
    uri: str
    media_type: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlignmentRunManifest:
    id: str
    purpose: Literal[
        "candidate_selection",
        "word_subtitles",
        "authoritative_retiming",
        "timing_eval",
        "untimed_text_alignment",
    ]
    schema_version: str
    created_at: str
    inputs: tuple[AlignmentArtifactRef, ...]
    backend: dict[str, Any]
    span_policy: str
    window_policy: dict[str, Any]
    outputs: tuple[AlignmentArtifactRef, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
```

`AlignmentRunManifest` is an index, not the whole result. Heavy rows remain
JSONL or external artifacts referenced by `AlignmentArtifactRef`.

Core should also define serializable records for:

- alignment windows;
- backend results;
- cue projections;
- candidate-level summaries;
- series-level summaries.

It should not define `current/`, `batch-00001.jsonl`, tarball names, or local
cache directories.

## Storage Backends

The first storage backend can be a local filesystem workspace. A later S3
backend should not require changing the core DTOs if artifacts are referenced by
URI:

```text
file:///.../.ja-media-runs/forced-align/...
s3://ja-media-runs/forced-align/...
```

A small workflow-owned `ArtifactStore` protocol is enough:

```python
class ArtifactStore(Protocol):
    def put_bytes(self, key: str, data: bytes, media_type: str | None = None) -> AlignmentArtifactRef: ...
    def open_text(self, ref: AlignmentArtifactRef) -> Iterator[str]: ...
```

This does not need to be in core until there are at least two real storage
implementations. Core can start with the `AlignmentArtifactRef` DTO only.

## Scenario Shapes

Candidate selection is an invocation-first workflow. It may align episode 1
against four or five cleaned SRT candidates, score residuals, and report:

- best candidate;
- additive offset estimate;
- drift/affine warning;
- suspicious cue examples.

It does not need a durable run by default. A `--save-run` flag can persist the
same records if the result is interesting.

Word-level ASS subtitles are artifact-first. If the user only wants an mpv
subtitle file, the ASS can be the output. If we want reproducible rebuilds,
persist the span policy, source text ref, audio ref, backend metadata, and
word-timing JSONL.

Authoritative retiming is run-first. Hundreds of files need replay, partial
failure handling, backend version tracking, and stable artifact refs. This is
where manifests, S3, and resumable stores become important.

Timing evaluation is run-first when it compares model versions or finetunes. The
important output is not only a retimed subtitle but the metric set and evidence
used to compute it.

Untimed text alignment can go either way. A one-off ASR transcript can be
invocation mode; a corpus of ASR outputs should be run mode.

## Design Consequence

Do not make "run manifest" a required input to `ForcedAlignerBackend.align()`.
The backend should see `Sequence[AlignmentWindow]` and return
`AlignmentWindowResult`s.

Do make the serialized window/result schemas stable enough that a workflow can
write them before and after backend execution. That lets lightweight workflows
stay light while large workflows gain replay by wrapping the same contract.
