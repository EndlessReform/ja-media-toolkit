# SRT Cleaning Batch Pipeline

## Current State

This branch has a working first slice in `packages/frontend`:

- `ja-media-srt-clean smoke-test` fetches AniList metadata and Kitsunekko subtitle inventory.
- `ja-media-srt-clean generate` writes OpenAI-compatible chat-completions JSONL, a manifest, a shard summary, and cached source SRTs.
- `ja-media-srt-clean reconstruct` consumes unordered OpenAI-style result JSONL and writes cleaned SRTs plus analysis logs.
- Tests cover window generation, custom IDs, shard limits, result parsing, unordered reconstruction, invalid rows, and source-level blocking errors.

The branch is not done. The biggest missing piece is the execution surface between generated JSONL and reconstructed output. Today the user still has to hand-run provider-specific commands and remember vLLM/OpenAI quirks, but generated artifacts now live in a predictable workspace.

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
  "pipeline_version": "clean:v1",
  "prompt_policy_sha256": "hex",
  "model": "RedHatAI/gemma-4-26B-A4B-it-NVFP4",
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
DecisionKind = Literal["as_is", "asis", "edit", "remove", "escalate"]

class CleanDecision(BaseModel):
    cue_id: int = Field(alias="id")
    decision: DecisionKind
    text: str | None = None
    category: str | None = None

class CleanWindowResult(BaseModel):
    decisions: list[CleanDecision]
```

`as_is` preserves the mechanically normalized cue baseline during reconstruction.
Legacy `asis` remains readable for older result artifacts. `edit` replaces text
while preserving timing, `remove` drops the cue, and `escalate` preserves the
raw original cue for human review.

## Batch Request Contract

The canonical request artifact is OpenAI-compatible JSONL for `/v1/chat/completions`:

```json
{
  "custom_id": "clean:v1:anilist-101573:srt-subtitle:w00001:1-10:policy-abcd:sha256-deadbeef",
  "method": "POST",
  "url": "/v1/chat/completions",
  "body": {
    "model": "gpt-5.5",
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
  "custom_id": "clean:v1:...",
  "pipeline_version": "clean:v1",
  "anilist_id": 101573,
  "subtitle_id": "abc123",
  "repo_path": "subtitles/anime_tv/Show/[Group] Show - 01.srt",
  "filename": "[Group] Show - 01.srt",
  "source_sha256": "hex",
  "cue_start_index": 1,
  "cue_end_index": 10,
  "active_indexes": [1, 2, 3],
  "window_number": 1,
  "model": "gpt-5.5",
  "prompt_policy_sha256": "hex",
  "local_cache_path": ".ja-media-runs/srt-clean/anilist-101573/current/sources/abc123.hash.srt",
  "metadata_warnings": []
}
```

`custom_id` is deterministic from pipeline version, AniList ID, subtitle ID, window number, source cue span, prompt policy hash, and active text hash. It is the join key between provider output and local reconstruction state.

## Local Rollout Strategy

Hosted batch rollout is not the short-term path. A cost pass on one all-episode show shard made the API option look silly compared with a local RTX 5090 finishing the same work in minutes with Gemma. Keep the generic OpenAI-compatible rollout driver as a useful someday tool, but optimize the immediate workflow for the local vLLM batch path.

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

Replace that with a small local wrapper, either a root script or a `ja-media-srt-clean run-vllm` subcommand:

```sh
uv run scripts/srt_clean_vllm_batch.py \
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
- Keep vLLM container startup separate from any hosted-provider rollout story.

The existing `scripts/oai_batch_rollout.py stats` calculator is still useful as a sizing/cost sanity check, but the async hosted rollout driver should move to backlog. If it returns later, it should remain a dumb pipe over OpenAI batch JSONL and not know about SRT cleaning.

Backlog shape for the generic hosted runner:

```sh
uv run scripts/oai_batch_rollout.py \
  --input .ja-media-runs/srt-clean/anilist-101573/current/batch-00001.jsonl \
  --out .ja-media-runs/srt-clean/anilist-101573/current/results.jsonl \
  --base-url http://localhost:8000/v1 \
  --model RedHatAI/gemma-4-26B-A4B-it-NVFP4 \
  --api-key-env VLLM_API_KEY \
  --concurrency 16
```

The stats subcommand can stay as a batch sizing tool:

```sh
uv run scripts/oai_batch_rollout.py stats \
  --input .ja-media-runs/srt-clean/anilist-101573/current/batch-00001.jsonl \
  --tokenizer o200k_base \
  --rate-limit-ktpm 30000 \
  --rate-limit-rpm 10000 \
  --safety-margin 0.80 \
  --input-price-per-mtok 1.25 \
  --cached-input-price-per-mtok 0.125 \
  --output-price-per-mtok 10.00
```

The stats command should render a Rich table with request count, shard path, stable system-prompt tokens, user-prompt min/p50/p95/max/total, estimated uncached and cached input tokens, output tokens, optional cost, and optional RPS/RPM/KTPM guesstimates. Rate limits use kilotokens per minute so provider quota pages can be copied without counting zeros. If RPM is supplied without KTPM, treat it as concurrency only.

```sh
uv run --project packages/frontend scripts/oai_batch_rollout.py tui \
  --input .ja-media-runs/srt-clean/anilist-101573/current/batch-00001.jsonl
```

The cost estimate is deliberately crude. Assume the system prompt is cacheable and the user prompt is variable. Treat output as `1.5x` input unless the user passes an override. This is an upper-bound planning tool, not billing truth.

## Reconstruction Contract

Reconstruction accepts one manifest and one or more provider output JSONL files. Provider output order is irrelevant.

Validation rules:

- Every expected window needs one successful result unless `--allow-partial` is set.
- A result `custom_id` must exist in the manifest.
- A source cue can receive only one decision.
- A window must contain exactly one decision for each active local ID.
- Decisions outside the active local ID range are errors.
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
- Columns for source index, time, original text, cleaned text, decision, category, and warning/error state.
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

1. Add a local vLLM Docker wrapper.
   This should remove the hand-edited `docker run` command, infer mounts and output names, and make local Gemma the happy path.

2. Add first-class smoke paths for the local path.
   One command should generate a tiny request set, run vLLM, reconstruct it, and summarize errors without manual path editing.

3. Add the cleaning review TUI.
   Reuse subsync/audio primitives to compare original vs cleaned text, inspect decisions, and optionally play the matching audio span.

4. Add retry/DLQ ergonomics.
   Failed provider calls and schema failures should become an obvious rerun input, not a manual JSONL archaeology task.

5. Add model/body override hooks to generation or local execution.
   Some compatible runtimes diverge on structured output support. The workflow needs a clean way to disable strict JSON schema, change temperature, or add provider-specific extra body fields without corrupting the manifest contract.

6. Add local media episode resolution for alignment.
   Start with arbitrary folders and existing filename heuristics. Use services for AniList matching when available, but do not require Docker for local alignment experiments.

7. Define the alignment input manifest.
   It should join `cleaned_srt_path`, source subtitle identity, media path, episode identity, VAD region timings, cue indexes, and aligner settings.

8. Add an end-to-end fixture.
   Use a small checked-in or generated audio/SRT fixture to prove generate -> rollout smoke -> reconstruct -> alignment manifest creation.

9. Keep the generic hosted rollout driver as nice-to-have.
   It is still useful for non-cleaning workflows or machines without local GPU, but it is not the current bottleneck.

## Non-Ergonomic Spots

- Local vLLM execution is disconnected from generation and reconstruction.
- vLLM batch usage leaks Docker, cache mounts, model flags, and path rewriting into the user's working memory.
- Output naming does not yet make the next command obvious.
- There is no single small safe smoke-test path.
- Error recovery exists as data, but not yet as a pleasant command.
- There is no pleasant way to review model decisions against original text and audio.
- The alignment destination is still conceptual, so it is unclear when a cleaned SRT is good enough.
- The CLI entry module has absorbed too many responsibilities.

## Suggestions

- Make local vLLM the short-term happy path.
- Keep the generic OpenAI-compatible JSONL executor as backlog, not as the next blocker.
- Wrap vLLM Docker execution directly enough to remove path/caching mistakes.
- Reuse subsync Textual and audio primitives for cleaning review.
- Make local folders a first-class alignment input, even if service metadata is used opportunistically.
- Prefer manifest-driven resumes and reruns everywhere. The user should rarely hand-edit JSONL.
- Add next-command hints after generation and vLLM execution. The CLI should print the exact reconstruct and review commands for the artifacts it just wrote.
- Keep cleaned SRTs source-clocked. Do not retime them during cleaning; timing changes belong to alignment/subsync stages.
