# SRT Cleaning Batch Pipeline Design

## Current One-Off Usage

The first implementation lives in `packages/frontend` as the
`ja-media-srt-clean` script. It expects the local LAN metadata/subtitle services
to be reachable through normal toolkit config, and it writes generated artifacts
next to the `--out` prefix:

```sh
cd packages/frontend
uv run ja-media-srt-clean generate \
  --anilist 101573 \
  --episode-one-only \
  --out /tmp/ja-media-srt-clean/101573

uv run ja-media-srt-clean generate \
  --anilist 101573,183385 \
  --episode-one-only \
  --out /tmp/ja-media-srt-clean/101573-183385
```

Generated files use gitignored names:

- `<prefix>.batch-00001.jsonl`: OpenAI-compatible request JSONL.
- `<prefix>.manifest.jsonl`: durable local reconstruction manifest.
- `<prefix>.shards.json`: shard summary.
- `<prefix>.sources/`: cached source SRTs.

Generation sends only active cues by default. Surrounding cue context is opt-in
with `--context-cues N`; keep it off unless a model has proven it can ignore
non-active cue indexes reliably.

After a provider/vLLM smoke run returns OpenAI-style batch output, reconstruct
with:

```sh
cd packages/frontend
uv run ja-media-srt-clean reconstruct \
  --manifest /tmp/ja-media-srt-clean/101573.manifest.jsonl \
  --batch-output /path/to/provider-output.jsonl \
  --out-dir /tmp/ja-media-srt-clean/reconstructed-101573
```

Reconstruction writes `decisions.jsonl`, `errors.jsonl`, `dlq.jsonl`, cleaned
SRTs, and a `cleaned-srts.tar.gz` archive unless `--no-archive` is passed.
`decisions.jsonl` is the model-evidence log, so it includes parsed decisions
even when a source SRT is skipped, with `compliant`, `within_active_span`, and
`noncompliant_reasons` fields for introspection.

## Status

Draft for review. This document proposes the first implementation slice for
generating LLM batch rows from Kitsunekko SRT candidates, then reconstructing
cleaned subtitle artifacts from batch output.

No implementation should proceed until the decision gates below are approved.

## Feasibility

This is feasible with the current repository shape.

The core services already expose the two durable data sources the pipeline
needs:

- `ja_media_core.anilist_search.HttpAniListSearchClient.anime()` can fetch a
  projected AniList metadata row by ID.
- `ja_media_core.kitsunekko.HttpKitsunekkoSubtitlesClient` can list subtitles
  by AniList ID and download individual file content by `subtitle_id`.
- `ja_media_core.transcripts.parse_srt()` and `format_srt()` already preserve
  original cue indexes, timing settings, text, and stable source-clock seconds.
- `ja_media_core.media_filename.suggest_ordinary_episode()` provides the
  conservative existing episode parser; the Kitsunekko service also has runtime
  episode filtering in
  `envs/services/src/ja_media_services/kitsunekko_subtitles/episode.py`.

The main risk is not API availability, but result provenance. Batch result
ordering is explicitly non-stable for OpenAI Batch, so every generated request
must carry a collision-resistant `custom_id` and a local manifest row with the
same key. The OpenAI Batch docs cap each input file at 50,000 requests and
200 MB; the generator should shard across multiple JSONL files before either
limit, not fail the whole job.

## Recommended Placement

Start as an experimental root script, not `packages/frontend`.

Reasoning:

- This may be a one-off experiment to produce one batch corpus and learn
  whether the workflow is worth keeping.
- Root scripts can still use `uv run` and import first-party packages without
  committing to a permanent `ja-media` CLI surface.
- The design should preserve clean promotion paths, but the public command
  should wait until the aligner experiment proves value.

Proposed first slice:

- `scripts/exploration/subtitle_cleaning_batch.py`
- `scripts/exploration/README.md` usage notes
- focused tests only if helpers are promoted out of the script

Promotion gate: move to `packages/frontend` only after one successful
end-to-end batch, reconstruction, cleaned-SRT aligner run, and a clear
expectation that this will be reused.

## Input Scope

The batch generator should accept AniList IDs in either form:

- `--anilist 101573,12345,67890`
- `--anilist-file ids.txt`, one integer per non-empty line, with `#` comments
  ignored.

Subtitle selection knobs:

- `--window-size N`, default `10`.
- `--context-cues N`, default `0`, used only as opt-in prompt context around
  the active window.
- `--group-prefix PREFIX`, repeatable, filtering `repo_path` or filename to
  candidates whose leading path/name starts with the prefix.
- `--episode-one-only`, opt-in, filtering through the existing conservative
  episode parser.
- `--language ja|unknown|all`, default `all` for the first slice unless review
  prefers Japanese-only LID filtering.

For the episode-one experiment, prefer local filtering over a service change:
fetch the full AniList file list, keep supported `.srt` rows, parse the filename
stem with `suggest_ordinary_episode()`, and retain only episode `1`. This keeps
the experiment cheap and auditable.

## Metadata Source

Use the local AniList search service as the metadata engine:

```python
fields = (
    "title_english",
    "title_native",
    "title_romaji",
    "description",
    "characters",
)
metadata = HttpAniListSearchClient().anime(anilist_id, fields=fields)
```

The service's direct AniList fallback already requests `characters` including
character full/native names and voice actor names. The local CSV-backed dataset
may or may not have equally rich character JSON for every row; the generator
should tolerate missing character details and include a `metadata_warnings`
array in its manifest.

Open question: whether `description` should be HTML-stripped for prompt context.
Recommendation: yes, reuse the plain-text description logic currently in
`packages/frontend/src/ja_media_frontend/audio_library/metadata.py` or move a
small helper into this package.

## Prompt And Structured Output Contract

The prompt should combine:

1. `MOVETHIS-house-style.md` verbatim as the system/developer cleaning policy.
2. Series context: English/Japanese/Romaji titles, synopsis, and compact
   character list.
3. One active cue window plus before/after context.

The response model should wrap the house-style row schema in an object, because
top-level arrays are awkward for some structured-output validators. Proposed
shape:

```python
class CleanDecision(BaseModel):
    index: int
    decision: Literal["asis", "edit", "remove", "escalate"]
    text: str | None
    category: CleanCategory | None


class CleanWindowResult(BaseModel):
    decisions: list[CleanDecision]
```

The generator should use this Pydantic model to emit JSON Schema into each
provider-specific request body. For OpenAI Chat Completions, the current docs
support `response_format={"type": "json_schema", "json_schema": ...}` and the
Python SDK also supports Pydantic models for synchronous parse calls. For batch
JSONL, we should emit the raw JSON Schema body, because the batch file is just
endpoint parameters serialized per line.

## Batch Row Shape

Use OpenAI-compatible JSONL as the first concrete output target. Official docs
say a batch row `body` uses the same parameters as the underlying endpoint, and
the OpenAPI spec confirms `/v1/chat/completions` is a supported batch endpoint.
Minimal row:

```json
{
  "custom_id": "clean:v1:anilist-101573:srt-abcd1234:w0007:sha256-deadbeef",
  "method": "POST",
  "url": "/v1/chat/completions",
  "body": {
    "model": "gpt-5.5",
    "messages": [],
    "response_format": {"type": "json_schema", "json_schema": {}}
  }
}
```

The command should also write sidecar manifests:

- `<out>.manifest.jsonl`: one row per window with `custom_id`, AniList ID,
  subtitle ID, `repo_path`, original filename, source SHA-256, cue index span,
  window number, model target, prompt-policy hash, and local cache path.
- `<out>.shards.json`: shard paths, request counts, byte sizes, model, endpoint,
  and enough metadata to submit each shard independently.
- `<out>.sources/`: downloaded source SRT files named by subtitle ID and source
  hash.

The custom ID should be deterministic from pipeline version, AniList ID,
subtitle ID or file hash, cue span, prompt-policy hash, and active cue text
hash. This lets failed windows be regenerated without changing successful IDs.

## Reconstruction

Add a second subcommand that consumes:

- OpenAI-style batch output JSONL.
- The generator manifest JSONL.
- The cached source files directory.

Outputs:

- `decisions.jsonl`: one row per cue decision for DuckDB analysis.
- `errors.jsonl`: invalid rows, failed API responses, schema errors, missing
  cues, duplicate decisions, or index mismatches.
- `<stem>.cleaned.srt` for each sane source SRT.
- `cleaned-srts.tar.zst` or `cleaned-srts.tar.gz`.

Reconstruction rules:

- `asis`: keep original cue text and timing.
- `edit`: keep timing, replace text with `text`.
- `remove`: drop the cue.
- `escalate`: keep original cue text by default and mark the decision in
  `decisions.jsonl`; optionally support `--drop-escalated` later.

Validation gates for a source file:

- Every non-overlapping window decision must reference indexes present in the
  source SRT.
- A cue may receive only one decision unless overlap is explicitly enabled in a
  future design.
- Missing windows make the source incomplete unless `--allow-partial` is set.
- The cleaned SRT uses `format_srt()` for sequential indexes, while
  `decisions.jsonl` preserves original indexes.

## OpenAI SDK Dependency

Do not require `openai` for offline generation, but add an SDK-backed validation
gate before trusting the payload shape.

Receipts:

- The Batch guide says each JSONL row contains one request and the row `body`
  has the same parameters as the underlying endpoint.
- The Batch OpenAPI spec requires `input_file_id`, `endpoint`, and
  `completion_window`; supported endpoints include `/v1/chat/completions`; each
  uploaded batch input file can contain up to 50,000 requests and 200 MB.
- The Chat Completions reference says `response_format: {"type": "json_schema",
  "json_schema": {...}}` enables Structured Outputs.
- The Structured Outputs guide says SDKs support Pydantic/Zod helpers and
  recommends avoiding schema/type divergence.

Recommendation: generate JSONL offline, but implement `--validate-openai-one`
as an optional smoke test before a real batch. If approved, add `openai` with
`uv add openai` from the relevant package/script environment and use the SDK's
structured-output/Pydantic support to validate one representative window.

If the script remains in `scripts/exploration`, do not add `openai` to
`packages/frontend` just for validation. Use the environment that owns the
script invocation, or defer SDK validation until promotion.

## Provider Portability

Use OpenAI-compatible JSONL as the canonical first artifact, but keep provider
details isolated in `batch.py`.

Potential future targets: `openai-chat-completions`, `openai-responses`,
`openrouter-chat-completions`, and `vllm-chat-completions`.

For non-OpenAI targets, the stable local manifest and `custom_id` remain the
reconstruction contract; only request body generation changes.

## Project Gates

Gate 1: Placement. Approve root `scripts/exploration/` first, with promotion to
`packages/frontend` only after a successful end-to-end experiment.

Gate 2: Metadata. Approve `HttpAniListSearchClient.anime()` and local GraphQL
fallback, with no direct AniList dependency in the CLI.

Gate 3: Subtitle Scope. Approve `.srt` only for the first slice. ASS can be
normalized later through existing `parse_ass()` and `format_srt()`.

Gate 4: Windowing. Approve non-overlapping windows with separate context cues.

Gate 5: Batch Body. Approve OpenAI Chat Completions JSONL as the first target,
with sharding and an optional SDK-backed one-row validation gate.

Gate 6: Escalations. Approve keeping escalated cues unchanged in `.cleaned.srt`
while recording them in `decisions.jsonl`.

Gate 7: Compression. Approve `tar.gz` as the portable default. Use `tar.zst`
only if we confirm `zstd` is available in the target environment.

## Implementation Plan After Approval

1. Add Pydantic models and tests for ID parsing, metadata context formatting,
   windowing, custom ID determinism, and reconstruction validation.
2. Add subtitle inventory/download helpers using `HttpKitsunekkoSubtitlesClient`.
3. Add sharded batch JSONL generation and manifest/source cache writing.
4. Add reconstruction from batch output to decisions JSONL and cleaned SRTs.
5. Add optional OpenAI SDK one-row validation if approved.
6. Run `uv run pytest packages/frontend/tests packages/core/tests/test_srt.py`
   from the repo root or `uv run pytest tests` from `packages/frontend`,
   depending on touched files.

## References

- Local policy input: `MOVETHIS-house-style.md`
- Local batch semantics input: `DELETETHIS-oai-batch-api-semantics.md`
- OpenAI Batch API docs:
  `https://developers.openai.com/api/docs/guides/batch`
- OpenAI Structured Outputs docs:
  `https://developers.openai.com/api/docs/guides/structured-outputs`
- OpenAI Python SDK:
  `https://github.com/openai/openai-python`
