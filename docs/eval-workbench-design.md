# Evaluation Workbench Design

This document describes a separate evaluation workbench for measuring subtitle
timing quality. It is related to ja-media-toolkit, but it is not the
learner-facing product. The learner-facing `ja-media` CLI should stay focused on
media management, transcription, subtitle review, and mining workflows. The
evaluation workbench is for research and analysis: it runs repeatable pipelines,
records what data was used, stores derived artifacts, and lets an analyst inspect
which subtitle sources are good enough to trust.

## Current Business Requirement

The requirement of the moment is:

> Given one or more test shows identified by AniList ID, estimate how good the
> subtitle timing is for each episode and candidate subtitle source.

Today, the best available path is to use real subtitle files from Kitsunekko or
similar sources. A candidate subtitle file is cleaned first, then aligned against
episode audio, then graded. The grader should answer practical questions: is
this subtitle file globally shifted by a consistent offset; does that offset
drift over the episode; which cues are missing, duplicated, badly split, or
suspicious; is this subtitle source good enough to use as a training or mining
input; and which show or episode should an analyst inspect next?

In the future, the same workbench should also handle predicted references, such
as ASR transcripts, TTS source text, generated subtitle candidates, or retimed
subtitles produced by a model. The design must not assume that every reference
starts as an SRT file. It also must not assume that every output is a cue list.

## What We Have Now

The repo already has several pieces that should be reused rather than rebuilt.

The services layer exposes media and metadata through HTTP APIs. Important
inputs include AniList identities for series-level selection, episode audio or
video material exposed by the anime-audio service, subtitle candidate files
exposed by subtitle mirror services such as Kitsunekko, and crosswalk services
that help resolve the same show across sources. These services are data
providers. They are not the evaluation registry. The workbench should call them,
copy the bytes or normalized records it used into durable storage, and record
both the original service URI and the materialized artifact URI.

The SRT cleaning work already produces cleaned subtitle labels from messy source
files. This is a transformation from one subtitle artifact into another:

```text
RawSubtitleSet -> CleanedSubtitleSet
```

The cleaning step may use a vLLM server and may need substantial VRAM. It should
be recorded as one node in an evaluation graph, not hidden inside an analyst's
folder naming convention.

The new inference environment contains a Qwen3 forced-alignment adapter. It can
take source text, split it into nagisa tokens, send timestamp markers to a vLLM
`/pooling` server, and merge word timings back into caller-owned groups. This is
a transformation over typed artifacts:

```text
AudioAsset + ReferenceTextOrCueGroups -> WordAlignmentSet + GroupAlignmentSet
```

The group abstraction matters. A group can be one SRT or ASS cue, one line from
untimed TTS source text, one ASR segment, one paragraph or sentence from a
generated transcript, or later one ASS karaoke segment with finer styling
metadata.

`packages/core` already defines durable audio, transcript, ASR, VAD, and forced
alignment contracts. Those should remain lightweight and reusable. The eval
workbench can use those contracts, but it should not force the learner-facing
toolkit to depend on eval-specific orchestration, storage, or experiment
tracking code.

## What We Need

The missing piece is not another learner-facing command. The missing piece is an
evaluation workbench that can record and replay a graph of artifact-producing
work.

For the current timing-quality evaluation, the graph looks like this:

```text
AniList show/episode selection
  -> fetch episode audio
  -> fetch candidate subtitle files
  -> parse subtitles into cue sets
  -> clean subtitle labels
  -> align cleaned labels against audio
  -> grade original/candidate timing against alignment output
  -> publish reports for analyst inspection
```

There are several important details in that graph. Cleaning and alignment may
not run in the same sitting because they may require different vLLM servers and
different VRAM budgets. The workbench must support stopping after cleaning,
later resuming alignment, and later rerunning only the grader. Data may move
across machines: a laptop may select episodes and inspect reports, a workstation
may run vLLM servers, and future cloud GPU machines may run expensive alignment
or training jobs. The registry of record cannot be "the folder I happened to
SCP."

The workbench must also support introspection. An analyst should be able to ask:
which AniList IDs and episode numbers are in this run; which source subtitle
files were evaluated; which cleaning model and prompt policy produced the
cleaned cue set; which aligner model and prompt layout produced the word
timings; which artifacts are missing; and which grades are stale because an
upstream artifact changed?

Finally, this needs to lead naturally into future training work. If a subtitle
source grades well, it may become an input to an SFT job or an RLVR job. The
workbench should record datasets and derived artifacts in a way that a future
training job can consume without scraping analyst report folders.

## The Evaluation Graph

The central object is a graph of typed artifacts and transformations. An
artifact is a piece of data with a type, a URI, a content hash when possible, and
metadata. Examples include `AniListSeriesRef`, `EpisodeMediaRef`, `AudioAsset`,
`RawSubtitleFile`, `RawCueSet`, `CleanedCueSet`, `ReferenceTextGroupSet`,
`WordAlignmentSet`, `CueAlignmentSet`, `TimingGradeReport`, and
`TrainingCandidateSet`.

A node is one transformation that consumes artifacts and produces artifacts.
Examples:

```text
FetchEpisodeAudio:
  AniListEpisodeRef -> AudioAsset

FetchCandidateSubtitles:
  AniListEpisodeRef -> RawSubtitleFile[]

ParseSubtitleCues:
  RawSubtitleFile -> RawCueSet

CleanSubtitleLabels:
  RawCueSet -> CleanedCueSet

QwenForcedAlignment:
  AudioAsset + CleanedCueSet -> WordAlignmentSet + CueAlignmentSet

TimingQualityGrade:
  RawCueSet + CleanedCueSet + CueAlignmentSet -> TimingGradeReport

ReportBuild:
  TimingGradeReport[] -> AnalystReport
```

Nodes are not subcommands in a fixed lifecycle. They are executable steps in a
graph. The current graph happens to contain fetch, clean, align, grade, and
report nodes. A future graph for TTS alignment might skip subtitle fetching. A
future graph for ASR evaluation might replace cleaned cue sets with ASR segment
sets. A future training graph might consume high-quality aligned cue sets and
produce a fine-tuning dataset.

## Storage Model

The workbench needs a storage backend with two implementations from the start:
local filesystem storage for fast development and one-machine debugging, and
S3-compatible storage for laptop/workstation/cloud cooperation. Garage already
exists on the LAN, so S3 is not a hypothetical future requirement. It should be
part of the first useful eval spike.

The storage backend should expose simple operations: put file, get file, put
JSON, get JSON, list by prefix, check existence, and record content hash and
size. Application code should not scatter raw `boto3` calls everywhere. It
should receive an artifact store object and write typed artifact refs through
that object. The backing store can be local paths or Garage S3.

Large data should live as files or objects in the artifact store. Metaflow or
any future run tracker should store references to those objects, not giant media
blobs embedded as Python artifacts.

## Garage Layout

Use a dedicated Garage bucket for evaluation artifacts. A reasonable first
bucket name is:

```text
ja-media-eval
```

If future training artifacts grow large or need different retention rules, add a
second bucket later:

```text
ja-media-training
```

The first bucket should be enough for the eval spike. Use prefixes to separate
run records, shared cached inputs, reports, and exported datasets:

```text
s3://ja-media-eval/runs/<run-id>/run.json
s3://ja-media-eval/runs/<run-id>/inputs/...
s3://ja-media-eval/runs/<run-id>/nodes/<node-id>/...
s3://ja-media-eval/runs/<run-id>/reports/...
s3://ja-media-eval/cache/anilist/<anilist-id>/...
s3://ja-media-eval/cache/audio/<audio-hash>/...
s3://ja-media-eval/cache/subtitles/<subtitle-hash>/...
s3://ja-media-eval/datasets/<dataset-id>/...
```

Use at least two principals: `ja-media-eval-writer`, used by laptop and
workstation jobs that materialize artifacts, and `ja-media-eval-reader`, used by
report viewers, notebooks, and inspection tools. If cloud compute is added,
create a separate principal for that environment so LAN workstation credentials
are not copied into cloud machines. The cloud principal should have the minimum
prefix access needed for the job.

## Project URI Schemas

The workbench should distinguish source data URIs from materialized artifact
URIs. Source URIs name data where it originally came from:

```text
anilist://series/<anilist-id>
anilist://series/<anilist-id>/episodes/<episode-number>
anime-audio://anilist/<anilist-id>/episodes/<episode-number>/audio
kitsunekko://anilist/<anilist-id>/episodes/<episode-number>/<candidate-id>
```

Artifact URIs name bytes or records that the eval system has materialized:

```text
file:///absolute/path/to/local/eval/artifact
s3://ja-media-eval/runs/<run-id>/inputs/audio.flac
s3://ja-media-eval/runs/<run-id>/nodes/clean-subtitles/cleaned-cues.json
s3://ja-media-eval/runs/<run-id>/nodes/qwen-align/cue-alignments.json
```

The distinction matters because source services can change. A run should record
both the source URI that was requested and the artifact URI and hash for the
exact bytes or normalized records that were actually evaluated.

## Metaflow's Role

Metaflow is a candidate substrate for execution history, step artifacts, resume
behavior, and cross-machine storage. It should not own the domain model. The
domain model is still typed media and eval artifacts.

Metaflow can help with assigning run IDs, recording step-level outputs, storing
small metadata artifacts, resuming after a failed or intentionally stopped step,
inspecting runs from a separate process, using S3-compatible storage as the
shared datastore, and later moving individual steps to remote compute. It should
store small records such as artifact refs, metrics, selected parameters,
diagnostics, and node status. The actual media files, cue JSON, alignment JSONL,
reports, and datasets should live in the artifact store.

The first Metaflow spike should answer concrete questions:

1. Can Metaflow use Garage's S3-compatible API cleanly as its datastore?
2. Can laptop and workstation see the same flow run and artifacts?
3. Can a run stop after cleaning and later resume alignment without manually
   carrying folder names around?
4. Can the Metaflow client API inspect run inputs, outputs, metrics, and failed
   steps clearly enough for an analyst tool?
5. Can we avoid pickling large or unstable domain objects into Metaflow itself?

If the answer is yes, Metaflow earns its keep. If not, the fallback is a small
internal graph/run manifest on top of the same artifact store.

## Eval Package Shape

Create a new eval package or environment that is separate from the `ja-media`
learner-facing CLI. The likely initial shape is:

```text
envs/eval/
  src/ja_media_eval/
    artifacts.py
    stores.py
    graph.py
    metaflow_flows/
    nodes/
      fetch_audio.py
      fetch_subtitles.py
      clean_subtitles.py
      qwen_align.py
      timing_grade.py
      report_build.py
```

This package can depend on Metaflow, S3 tooling, reporting libraries, and the
heavy inference environment when needed. It can also depend on `packages/core`
for shared cue/audio/alignment contracts. The package should expose analyst and
developer tools eventually, but those tools should be graph/run inspection
tools, not learner-facing `ja-media` subcommands.

## Presentation Layer

The analyst-facing output should be built from grade artifacts, not from ad hoc
local folders. Initial outputs can be a Markdown or HTML report per run, a CSV or
JSON table of episode-level scores, cue-level diagnostic JSON for deeper
inspection, and links to source subtitle, cleaned cue set, alignment output, and
audio.

Later, this can become a TUI or local web UI. That UI should read the run graph
and artifact refs. It should not become the registry of record.

## Future Training Work

Timing evaluation is the first use case, but the same artifact graph should
support training data production. A future SFT or RLVR job might consume cleaned
and well-timed cue sets, aligned word timings, bad examples with diagnostic
labels, model-generated retimings, and human accept/reject decisions.

Those should be exported as dataset artifacts with their own refs and hashes:

```text
s3://ja-media-eval/datasets/<dataset-id>/manifest.json
s3://ja-media-eval/datasets/<dataset-id>/examples.jsonl
```

If training becomes large enough, the data can move or copy into a dedicated
training bucket. The eval graph should still record where each dataset came
from.

## First Implementation Slice

The first slice should be deliberately small but real:

1. Create `envs/eval`.
2. Add typed artifact refs and a storage backend interface.
3. Add local filesystem and Garage S3 implementations.
4. Configure a dedicated Garage bucket and writer/reader principals.
5. Build one Metaflow spike over the existing TTS alignment fixture.
6. Replace the TTS fixture with one real AniList episode and one candidate SRT.
7. Record cleaned cues, Qwen alignment output, and a simple timing grade.
8. Generate a human-readable report from the stored grade artifacts.

The first grader can be simple. It only needs enough signal to validate the
workbench: cue coverage, monotonicity failures, median and percentile timing
offsets, drift over episode time, and a list of cues whose alignment is
suspicious.

After that works, improve scoring and presentation. Do not build a general DAG
scheduler before the artifact store, typed refs, and Metaflow spike have proved
their value.
