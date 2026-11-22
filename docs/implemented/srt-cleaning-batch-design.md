# SRT Cleaning Batch Pipeline

## Current State

This branch has a working first slice in `packages/frontend`:

- `ja-media-srt-clean smoke-test` fetches AniList metadata and Kitsunekko subtitle inventory.
- `ja-media-srt-clean generate` writes OpenAI-compatible chat-completions JSONL, a manifest, a shard summary, and cached source SRTs.
- `ja-media-srt-clean run-vllm` wraps the local vLLM batch invocation and handles the expected Docker mount shape.
- `ja-media-srt-clean run-provider` executes one shard against OpenAI, DeepSeek, or a custom OpenAI-compatible endpoint, then reconstructs it for review.
- `ja-media-srt-clean reconstruct` remains an escape hatch for imported or asynchronous OpenAI-style result JSONL.
- `ja-media-srt-clean review` opens the cleaning review surface over source SRTs, cleaned SRTs, decisions, errors, and optional audio.
- Tests cover window generation, custom IDs, shard limits, local vLLM command construction, result parsing, unordered reconstruction, review loading, invalid rows, and source-level blocking errors.

The branch is not the final evaluation system. It now proves the cleaning slice
and gives it a predictable workspace, but cross-machine artifact storage,
alignment orchestration, grading, and analyst reporting belong to the newer
evaluation-workbench direction.

The CLI entrypoint has been split into smaller modules. Keep it that way: add future behavior to focused modules under `ja_media_frontend.srt_cleaning`, not to the script entrypoint.

## Goal

Produce cleaned Japanese subtitle text that is faithful enough to feed an audio-aware forced aligner.

Durable stages:

1. Discover subtitle candidates from local services.
2. Convert source SRT cues into deterministic cleaning windows.
3. Run those windows as OpenAI-compatible batch rows, with local vLLM as the happy path.
4. Reconstruct complete cleaned SRTs from unordered provider results.
5. Pair cleaned SRT cues with local audio/VAD windows for forced alignment.

Models, providers, batch APIs, and GPU runtimes can change. The manifest, source cache, decisions log, and cleaned SRTs are the durable artifacts.

## Current Usage

Run commands from the frontend package:

```sh
cd packages/frontend
```

Optional sanity check before generation:

```sh
uv run ja-media-srt-clean smoke-test \
  --anilist 101573 \
  --episode-one-only \
  --preview-srt
```

Generate batch rows into the default workspace:

```sh
uv run ja-media-srt-clean generate \
  --anilist 101573 \
  --episode-one-only
```

This clobbers the previous workspace run by default and writes:

```text
../../.ja-media-runs/srt-clean/anilist-101573/current/
├── run-manifest.json
├── batch-00001.jsonl
├── manifest.jsonl
├── shards.json
└── sources/
```

Run the current local vLLM path by mounting that run directory as `/data`:

```sh
cd ../..
docker run --rm --runtime nvidia --gpus all \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v vllm-cache:/root/.cache/vllm \
  -v "$PWD/.ja-media-runs/srt-clean/anilist-101573/current:/data" \
  -e VLLM_SKIP_MODEL_NAME_VALIDATION=1 \
  -e HF_HOME=/root/.cache/huggingface \
  --entrypoint vllm vllm/vllm-openai run-batch \
  -i /data/batch-00001.jsonl \
  -o /data/results.jsonl \
  --model RedHatAI/gemma-4-26B-A4B-it-NVFP4 \
  --max-model-len 96000 \
  --max-num-batched-tokens 16384
```

Reconstruct from the workspace once `results.jsonl` exists:

```sh
cd packages/frontend
uv run ja-media-srt-clean reconstruct \
  --anilist 101573
```

That writes:

```text
../../.ja-media-runs/srt-clean/anilist-101573/current/reconstruct/
├── decisions.jsonl
├── errors.jsonl
├── dlq.jsonl
├── cleaned/
└── cleaned-srts.tar.gz
```

Useful variants:

- Add `--run-hash` to `generate` when you want a preserved `sha256-*` run instead of clobbering `current/`.
- Add `--run-id sha256-...` to `reconstruct` to reconstruct a preserved run.
- Add `--workspace-root /path/to/runs` if the default repo-local `.ja-media-runs/` is inconvenient.
- Add `--out PREFIX` to `generate` or `--manifest/--batch-output/--out-dir` to `reconstruct` for the legacy explicit-path flow.
- Use `uv run scripts/oai_batch_rollout.py stats --input ../../.ja-media-runs/srt-clean/anilist-101573/current/batch-00001.jsonl` from `packages/frontend` for the rough token/cost calculator.

Generated paths under the run directory:

- `batch-00001.jsonl`: OpenAI-compatible request JSONL.
- `manifest.jsonl`: local reconstruction manifest.
- `shards.json`: shard summary.
- `sources/`: cached source SRT files.

Reconstruction paths:

- `decisions.jsonl`: one parsed model decision per active cue.
- `errors.jsonl`: malformed rows, provider failures, schema errors, and source validation failures.
- `dlq.jsonl`: retry/review rows derived from errors.
- `cleaned/*.cleaned.srt`: source-clock SRTs after cleaning.
- `cleaned-srts.tar.gz`: portable archive, unless disabled.

Every executor also writes a sibling `*.execution.json` with the requested model
and model names returned by the server.

## Artifact Workspace

The default local workspace should be a repo-local, gitignored directory:

```text
.ja-media-runs/
└── srt-clean/
    └── anilist-101573/
        ├── current/
        │   ├── run-manifest.json
        │   ├── batch-00001.jsonl
        │   ├── manifest.jsonl
        │   ├── shards.json
        │   ├── sources/
        │   ├── results.jsonl
        │   └── reconstruct/
        │       ├── decisions.jsonl
        │       ├── errors.jsonl
        │       ├── dlq.jsonl
        │       ├── cleaned/
        │       └── cleaned-srts.tar.gz
        └── sha256-8f3a21c4d90b/
```

Default behavior:

- `generate --anilist 101573` writes to `.ja-media-runs/srt-clean/anilist-101573/current/`.
- A new `generate` run clobbers `current/` by default.
- `reconstruct --anilist 101573` autodetects `current/manifest.jsonl`, `current/results.jsonl`, and writes to `current/reconstruct/`.
- The vLLM wrapper autodetects `current/batch-00001.jsonl` and writes `current/results.jsonl`.
- The review TUI autodetects `current/manifest.jsonl`, `current/reconstruct/decisions.jsonl`, `current/reconstruct/errors.jsonl`, and the cached source SRTs.
- `--run-hash` writes to `sha256-<hash>/` instead of clobbering `current/`.
- `--workspace-root` can move the root, but the default remains repo-local so the whole state can be archived with `tar`.

`run-manifest.json` is the run-level index:

```json
{
  "schema_name": "ja-media.srt-clean.run",
  "schema_version": "1.0.0",
  "anilist_id": 101573,
  "run_id": "current",
  "created_at": "2026-06-28T00:00:00Z",
  "pipeline_version": "clean:v2",
  "prompt_policy_sha256": "hex",
  "model": "RedHatAI/gemma-4-26B-A4B-it-NVFP4",
  "requested_model": "RedHatAI/gemma-4-26B-A4B-it-NVFP4",
  "paths": {
    "batch_shards": ["batch-00001.jsonl"],
    "window_manifest": "manifest.jsonl",
    "shards_summary": "shards.json",
    "sources_dir": "sources",
    "results": "results.jsonl",
    "reconstruct_dir": "reconstruct"
  }
}
```

Every durable machine-read artifact should carry a semver `schema_version`.
Additive changes bump the minor version and remain readable. Breaking changes bump the major version; generate, reconstruct, rollout, and review commands must refuse mismatched major versions with a direct error instead of quietly interpreting stale state.

## Data Sources

Metadata comes from the local AniList search service:

```python
fields = ("title_english", "title_native", "title_romaji", "description", "characters")
metadata = HttpAniListSearchClient().anime(anilist_id, fields=fields)
```

Subtitle candidates come from the local Kitsunekko subtitles service:

```python
files = HttpKitsunekkoSubtitlesClient().anilist_files(anilist_id)
content = HttpKitsunekkoSubtitlesClient().file_content(subtitle_id)
```

The CLI should keep using toolkit config and first-party clients. Service URLs are not secrets and should not be hand-assembled in feature code.

## Cleaning Prompt Contract

The system message is `packages/frontend/src/ja_media_frontend/house-style.md`. It defines the normalization rules and the structured-output task.

Each request contains:

- Series context: AniList ID, English/native/Romaji titles, synopsis, and a compact character list.
- Optional context cues before and after the active span.
- Active cues with local request IDs.

Active cue IDs are local to one request. They are not source SRT indexes. Models must return exactly one decision for every active local ID and no decisions for context cues.

Current structured output:

```python
DecisionKind = Literal["as_is", "edit", "remove", "escalate"]

class CleanDecision(BaseModel):
    cue_id: int = Field(alias="id")
    decision: DecisionKind
    text: str | None
    reasons: list[CleanupReason]

class CleanWindowResult(BaseModel):
    decisions: list[CleanDecision]
```

`as_is` and `escalate` preserve the deterministic pre-clean baseline during
reconstruction. `edit` requires text and at least one cleanup reason, `remove`
requires a reason and drops the cue, and `escalate` requires an explanation.
An exact no-op `edit` is normalized to `as_is`; this avoids withholding a whole
window when the provider describes an edit but returns the supplied baseline
unchanged. The review loader still reads old reconstructed `category` rows so
retained runs remain inspectable.

## Batch Request Contract

The canonical request artifact is OpenAI-compatible JSONL for `/v1/chat/completions`:

```json
{
  "custom_id": "clean:v2:anilist-101573:srt-subtitle:w00001:1-10:policy-abcd:sha256-deadbeef",
  "method": "POST",
  "url": "/v1/chat/completions",
  "body": {
    "model": "gpt-5.6-luna",
    "messages": [],
    "response_format": {
      "type": "json_schema",
      "json_schema": {"name": "clean_window_result", "strict": true, "schema": {}}
    }
  }
}
```

Shard limits default to 50,000 requests and 200 MB per JSONL file. The row can be accepted by OpenAI Batch, compatible batch APIs, provider scripts, or local vLLM tooling. Reconstruction only requires OpenAI-style result rows keyed by `custom_id`.

## Manifest Contract

Each window has one manifest row:

```json
{
  "custom_id": "clean:v2:...",
  "pipeline_version": "clean:v2",
  "anilist_id": 101573,
  "subtitle_id": "abc123",
  "repo_path": "subtitles/anime_tv/Show/[Group] Show - 01.srt",
  "filename": "[Group] Show - 01.srt",
  "source_sha256": "hex",
  "cue_start_index": 1,
  "cue_end_index": 10,
  "active_indexes": [1, 2, 3],
  "window_number": 1,
  "model": "gpt-5.6-luna",
  "prompt_policy_sha256": "hex",
  "local_cache_path": ".ja-media-runs/srt-clean/anilist-101573/current/sources/abc123.hash.srt",
  "metadata_warnings": []
}
```

`custom_id` is deterministic from pipeline version, AniList ID, subtitle ID, window number, source cue span, prompt policy hash, and active text hash. It is the join key between provider output and local reconstruction state.

## Local Rollout Strategy

Local vLLM remains the cheap path for large corpus slices. Hosted providers are
useful for bounded model comparisons, so the same generated request rows can now
be sent through `run-provider` without changing reconstruction.

The rough edge right now is the Docker incantation:

```sh
docker run --rm --runtime nvidia --gpus all \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v vllm-cache:/root/.cache/vllm \
  -v "$PWD/.ja-media-runs/srt-clean/anilist-184591/current:/data" \
  -e VLLM_SKIP_MODEL_NAME_VALIDATION=1 \
  -e HF_HOME=/root/.cache/huggingface \
  --entrypoint vllm vllm/vllm-openai run-batch \
  -i /data/batch-00001.jsonl \
  -o /data/results.jsonl \
  --model RedHatAI/gemma-4-26B-A4B-it-NVFP4 \
  --max-model-len 96000 \
  --max-num-batched-tokens 16384
```

This has been replaced by `ja-media-srt-clean run-vllm`:

```sh
uv run ja-media-srt-clean run-vllm \
  --anilist 184591 \
  --model RedHatAI/gemma-4-26B-A4B-it-NVFP4 \
  --max-model-len 96000 \
  --max-num-batched-tokens 16384
```

Wrapper contract:

- Accept host paths and map them into one Docker `/data` mount automatically.
- Default Hugging Face and vLLM cache mounts to the known-good local layout.
- Print the exact Docker command before running it.
- Support `--dry-run` for copy/paste/debugging.
- Support `--image`, `--gpus`, `--runtime`, `--env KEY=VALUE`, and repeated `--extra-vllm-arg`.
- With `--anilist`, autodetect `.ja-media-runs/srt-clean/anilist-<id>/current/batch-00001.jsonl` and write `results.jsonl`.
- Infer an output path next to the input if `--out` is omitted.
- Refuse inputs outside the chosen data mount unless the user passes an explicit `--data-root`.
- Keep vLLM container startup separate from hosted-provider execution.

Run a bounded GPT-5.6 Luna comparison:

```sh
uv run ja-media-srt-clean run-provider \
  --anilist 184591 \
  --provider openai \
  --model gpt-5.6-luna \
  --limit 20 \
  --out ../../output/srt-clean/gpt-5.6-luna.jsonl
```

Run the same slice with DeepSeek V4 Flash:

```sh
uv run ja-media-srt-clean run-provider \
  --anilist 184591 \
  --provider deepseek \
  --model deepseek-v4-flash \
  --body-json '{"thinking":{"type":"disabled"}}' \
  --limit 20 \
  --out ../../output/srt-clean/deepseek-v4-flash.jsonl
```

The provider runner retries invalid model output once using the original cues,
the rejected response, and exact validation messages. A second invalid response
becomes `validation_retry_exhausted` and retains both attempts for the DLQ.
Transient network errors, HTTP 429, and retryable server responses use Tenacity
with jittered exponential backoff and provider `Retry-After` support. Transport
attempt counts are recorded on result rows and do not consume the one schema
repair attempt.
Hosted runs flush every returned row. `--resume` keeps locally valid results and
reruns missing or failed request IDs. Saved final responses from exhausted schema
repairs are also reused when they become valid under deterministic normalization.
`scripts/oai_batch_rollout.py stats` remains available for token and cost sizing.

## Reconstruction Contract

Reconstruction accepts one manifest and one or more provider output JSONL files. Provider output order is irrelevant.

Validation rules:

- Every expected window needs one successful result unless `--allow-partial` is set.
- A result `custom_id` must exist in the manifest.
- A source cue can receive only one decision.
- A window must contain exactly one decision for each active local ID.
- Decisions outside the active local ID range are errors.
- `edit` requires changed text and at least one cleanup reason.
- `remove` requires at least one cleanup reason.
- `escalate` requires a short explanation.
- A source with blocking errors is skipped unless partial output is explicitly allowed.

The cleaned SRT uses source timings and sequential formatted indexes. The decision log preserves original source indexes for analysis.

## Cleaning Review UI

Before forced alignment, add a review surface for checking whether the model cleaning is trustworthy. Reuse the subsync Textual/application primitives rather than inventing a separate interaction model.

Inputs:

- Source SRT cache path from the manifest.
- Cleaned SRT path from reconstruction.
- `decisions.jsonl` and `errors.jsonl`.
- Optional media path or resolved audio artifact.

Primary view:

- One row per source cue or decision.
- Columns for source index, time, original text, cleaned text, decision, reasons, and warning/error state.
- Filters for `edit`, `remove`, `escalate`, schema errors, changed text, and unchanged text.
- Diff-oriented cell rendering for base vs rewritten line.
- Jump/play controls using `MaterializedAudio` and `MaterializedAudioPlayer`.
- Accept/reject/mark-review actions written to a sidecar review JSONL.

This UI is a quality gate, not a proofreading sink. The first goal is to answer: is this model/prompt good enough to feed the aligner for this show?

## Alignment End State

Cleaned SRTs are not the final product. They are text candidates for a forced alignment workflow that needs local audio.

Likely end state:

1. Resolve a local media file or arbitrary media folder to AniList show and episode metadata.
2. Fetch or select cleaned subtitle candidates for that episode.
3. Split the audio into VAD windows using the existing subsync/audio strategy.
4. Join candidate subtitle cues to VAD/audio regions by source clock time.
5. Feed each region to the forced aligner with audio plus candidate text.
6. Produce scored aligned regions for mining, shadowing, or candidate ranking.

The audio server can help with media discovery and episode matching, but this should not require the full Docker service stack. A local-folder path should be first-class: given this directory of media files, find likely show/episode matches, then align against cleaned subtitles.

Important boundary: SRT cleaning can be service-backed because it depends on Kitsunekko/AniList mirrors. Alignment is local-media-backed because it depends on the user's actual audio file.

## What Is Left

1. Add local media episode resolution for alignment.
   Start with arbitrary folders and existing filename heuristics. Use services for AniList matching when available, but do not require Docker for local alignment experiments.

2. Define the alignment input manifest.
   It should join `cleaned_srt_path`, source subtitle identity, media path, episode identity, VAD region timings, cue indexes, and aligner settings.

3. Add an end-to-end real-media fixture.
   The checked-in TTS forced-alignment fixture proves the Qwen client path. The next useful fixture should use a real episode audio clip plus a candidate SRT to prove cleaning -> alignment input creation -> grading.

## Non-Ergonomic Spots

- Review judgments are not yet saved as a reusable artifact.
- The alignment destination is still conceptual, so it is unclear when a cleaned SRT is good enough.
- The local workspace is a folder convention, not a cross-machine artifact registry.

## Suggestions

- Make local folders a first-class alignment input, even if service metadata is used opportunistically.
- Prefer manifest-driven resumes and reruns everywhere. The user should rarely hand-edit JSONL.
- Add next-command hints after generation and vLLM execution. The CLI should print the exact reconstruct and review commands for the artifacts it just wrote.
- Keep cleaned SRTs source-clocked. Do not retime them during cleaning; timing changes belong to alignment/subsync stages.

## Proposed Corpus Review Slice

Status: proposed for review. The goal is interactive discovery of recurring cleanup
patterns plus a faster manual-review queue over the completed corpus.

### Textual application

Use one Textual application with two top-level tabs:

1. **Corpus Explorer** works across every series and release. It provides interactive
   tables and pivots for deleted characters, n-grams, label associations, edit
   shapes, and contiguous decision/reason runs. Selecting any row or pivot cell opens
   the matching cues rather than ending at a count or CSV.
2. **Cue Review** retains the per-series source list, timeline, audio, and original
   versus cleaned detail. Filters selected in Corpus Explorer become its review
   queue, so the reviewer can move through the exact subset that produced a pattern.

Both tabs support filters for:

- series, episode, release, and subtitle source;
- `edit`, `remove`, `escalate`, `as_is`, and missing/failed decisions;
- one or more cleanup reasons, with separate edit-reason and delete-reason pivots;
- deletion position and edit shape;
- mechanical-change status and model-change status;
- literal text or n-gram;
- reviewed/unreviewed and `correct`/`wrong`/`unsure` judgments.

Tables remain sortable by count, distinct-series support, association metrics, cue
duration, and source. Filters compose rather than replacing one another.

### Deleted-span extraction

Compare the mechanical baseline received by the model with the model's cleaned text.
Do not compare directly against the raw SRT for this analysis, because that would mix
known mechanical normalization with the model's work.

- `remove`: the whole mechanical cue is one deleted span.
- `edit`: use the standard-library diff to extract deleted spans.
- ambiguous or heavily rewritten edits: retain the whole pair in a `complex_diff`
  bucket rather than trying to force a clean interpretation.
- `as_is` and `escalate`: do not contribute deleted spans, but remain available for
  cue review and decision-run analysis.

Each derived span retains its source cue, cleaned text, decision, all reason labels,
position within the cue, AniList series, episode, release/source identity, and stable
cue identifiers. These are derived review records, not new production contracts.

### Character and n-gram exploration

The baseline includes character frequencies and character n-grams of lengths 1-4
over deleted spans. The Corpus Explorer can pivot these globally or by decision,
reason, series, release, and deletion position.

For every character or n-gram, show:

- raw deleted-span count;
- distinct series and episodes;
- number of subtitle sources;
- reason and decision distributions;
- representative matching edits;
- matching edits carrying other labels.

Repeated examples from multiple releases of one episode remain visible, but do not
count as independent series support.

### Label associations

The first association pass uses one observed character n-gram as the antecedent and
one existing cleanup reason as the consequent. It uses only the observed n-grams,
existing labels, and directly derived deletion position.

For each `n-gram -> reason` association, calculate:

- **support**: deleted spans containing the n-gram and carrying the reason;
- **distinct-series support**: independent series contributing matches;
- **confidence**: fraction of spans containing the n-gram that carry the reason;
- **coverage**: fraction of the reason's spans containing the n-gram;
- **lift**: confidence divided by the reason's overall frequency.

The table can be restricted to `edit` or `remove`, or to prefix, infix, suffix, or
whole-cue deletions. Selecting an association opens its matching cue pairs and the
same n-gram under other reasons. This makes a broad fragment such as `（` visibly
different from a more specific fragment that is concentrated under one label.

Use scikit-learn's character `CountVectorizer` for sparse binary n-gram extraction.
Start with n-grams 1-4 and `min_df=5`; expose `min_df` as an explorer control so it
can be raised to suppress rare fragments without changing code.

If single-n-gram associations reveal a concrete need for conjunctions, add a second
pass with `mlxtend` FP-growth/association rules, capped at two-item antecedents and a
minimum distinct-series support. This is deliberately conditional: do not generate
combinatorial rules until the baseline shows that one fragment is insufficient.

These associations describe what the model deleted and how its labels relate to the
deleted text. They do not by themselves authorize a deterministic cleaning rule.

### Edit-shape pivot

The edit-shape inventory is an interactive Corpus Explorer pivot, not merely a CSV.
Rows are:

- whole-cue deletion;
- single prefix, suffix, or infix deletion;
- substitution only;
- insertion;
- multiple disjoint edits;
- complex diff.

Columns can be decision, reason, series, or release. Selecting a cell opens those
cues in Cue Review. This is a way to stratify review and spot concentrations; it is
not presented as automatic rule discovery. CSV export remains available for ad hoc
checks.

### Cue track, runs, and navigation

Color cues by decision: green `as_is`, yellow `edit`, red `remove`, magenta
`escalate`, and gray missing/failed. Alternate two shades within each decision color
so adjacent cues remain visually separate, and preserve a distinct selected-cue
highlight.

Show a legend plus per-source decision and reason counts. Chronological track order
never changes. Next/previous actions move through the current filtered queue instead
of forcing the reviewer through every cue.

Run-length encoding groups contiguous decisions and dominant reasons. The Corpus
Explorer run table shows source, start/end cue and time, cue count, duration,
decision, reason distribution, and normalized episode position. It can be sorted by
length or duration and filtered by decision/reason. Selecting a run switches to Cue
Review and jumps to its first cue. This supports quick inspection of blocks such as
consecutive lyric removals without adding another analysis method.

### Cue review details

Default review includes model `edit`, `remove`, `escalate`, and missing/failed cues.
Keep separate deterministic spotcheck queues for:

- cues unchanged mechanically and accepted as `as_is`;
- cues changed mechanically and then accepted as `as_is`.

The detail panel shows a highlighted inline diff, the previous and next cue text,
decision and all reasons, mechanical baseline, timing, source/release identity, and
audio playback. Review judgments are `correct`, `wrong`, or `unsure` plus an optional
note, appended immediately to review JSONL.

### Parked follow-up

TF-IDF stays parked until character/n-gram frequency and association exploration
shows a specific gap. If needed, aggregate deleted spans by reason or source before
applying character n-gram TF-IDF; per-span documents are too short to be useful.

### Delivery boundary

Expected work is roughly two to three focused days: deleted-span aggregation and
association tables, Corpus Explorer, then timeline colors, filters, jumps,
persistence, and tests. No cleaning rule is promoted by this slice. The delivered
result is an interactive way to find and inspect recurring patterns, plus a faster
manual-review workflow.
