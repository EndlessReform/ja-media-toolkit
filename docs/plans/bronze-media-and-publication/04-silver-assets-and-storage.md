# Silver assets and storage

Status: proposed domain/storage contract; orchestration implementation is gated
on the spike.

## What silver means

Silver is a reusable claim or derived byte stream with explicit input and
recipe provenance. It is not a single quality level or one monolithic schema.

Candidate asset families include:

```text
episode_hint
episode_binding
audio_language
subtitle_normalization
subtitle_language
subtitle_alignment
subtitle_cleaning
portable_aac
demucs_stems
trimmed_audio
dialogue_clips
encodec_features
```

Only persist a boundary if it is expensive, reusable, independently selectable,
decision-bearing, shared across workflows, or part of a published dataset.
Scratch WAVs, temporary resamples, partial tensors, and parser intermediates
remain in the worker workspace.

## Transformation registry

Dagster asset definitions in checked-in code are the transformation registry.
Do not create a CRUD endpoint or SQL table containing every operation.

Each durable definition declares:

- stable asset/operation name;
- input asset families and partition relationship;
- code/recipe/model version;
- output roles and media types;
- resource hints;
- checks and blocking policy;
- storage adapter/I/O manager.

Adding a new transformation should normally add one definition and tests, not a
new microservice, API route family, and catalog schema.

## Row claims and byte artifacts

Silver has two storage shapes because it has two access patterns:

| Shape | Examples | Live location |
| --- | --- | --- |
| Small indexed claims | capture headers, hints, bindings, conflicts | `ja_media_data` PostgreSQL database |
| Large immutable outputs | normalized subtitles, AAC, stems, clips | Garage silver prefix |

PostgreSQL runs on flash-backed server storage and serves incremental point
lookups. It never stores audio or subtitle payloads. A Dagster snapshot asset
exports ledger tables to versioned Parquet under
`audio/anime/silver/catalog/`; routine lookups do not read those snapshots.

## Storage adapter

The Garage adapter controls where bytes live. The orchestrator records the
asset/partition/version and materialization metadata; it does not choose a
random storage location by itself.

Suggested layout:

```text
audio/anime/silver/assets/{asset_key}/{partition_key}/{data_version}/
  outputs/{role}.{extension}
  artifact.json
```

Escape/encode partition keys safely. Human titles are metadata, not paths.

`artifact.json` is a generic envelope written after verified outputs:

```json
{
  "schema_version": 1,
  "kind": "media-artifact",
  "result_id": "result-01J...",
  "asset_key": "portable_aac",
  "partition_key": "anilist-15451/e003",
  "data_version": "sha256:...",
  "code_version": "portable-aac-v1",
  "inputs": [
    {"asset_key": "episode_audio", "partition_key": "anilist-15451/e003", "data_version": "..."}
  ],
  "outputs": [
    {"role": "audio", "key": "...", "media_type": "audio/mp4", "size_bytes": 123, "sha256": "..."}
  ],
  "facts": {"duration_ms": 1421000, "codec": "aac", "channels": 2}
}
```

The envelope is shared. Operation-specific facts remain bounded JSON and
materialization metadata unless an application needs a durable typed contract.

## Lightweight references

Workers and asset functions pass `MediaArtifactRef`, not audio bytes:

```text
result_id
asset_key
partition_key
data_version
bucket/key or content locator
media type
size/hash
```

The compute process streams/downloads the referenced content directly from
Garage. The control plane never proxies large media.

Dagster's built-in pickle-to-S3 manager is not the intended media contract. A
custom reference-oriented I/O manager may manage paths and manifests while
asset code performs large streaming I/O explicitly when that is clearer.

## Version inputs completely

The data/cache version must reflect every output-affecting input:

```text
input artifact versions
code/recipe version
model/checkpoint identity
prompt or regex rules
parameters
relevant external-tool version
```

Do not equate Git commit alone with data version. Runtime-only choices such as
worker hostname, scratch directory, or concurrency do not change result identity.

## Commit behavior

1. allocate temporary workspace/prefix;
2. produce outputs;
3. verify format, size, hash, and required facts;
4. upload final immutable output keys;
5. write `artifact.json` last;
6. report successful materialization and metadata;
7. leave failed temporary uploads invisible and reap them by lifecycle rule.

Retries use a deterministic cache/idempotency key. A verified committed result
is adopted; partial output never becomes current.

## Concrete sidecar outputs

| Asset | Inputs | Durable outputs/checks |
| --- | --- | --- |
| `subtitle_normalization` | one raw track | canonical UTF-8 SRT, cue JSON, parser/rule version, cue counts |
| `subtitle_language` | normalized cues | language evidence, model/version, sampling policy |
| `subtitle_alignment` | subtitle, target audio, optional reference | aligned SRT/cues, offsets, unmatched cues, timing summary |
| `subtitle_cleaning` | normalized/aligned cues | cleaned SRT/cues and changed/removed counts |
| `portable_aac` | episode audio | AAC plus verified media facts |
| `demucs_stems` | audio | named stems plus model/checkpoint |
| `trimmed_audio` | audio and edit decision | audio plus explicit edit list |
| `dialogue_clips` | selected audio/subtitles | sample manifest and clips or a packed clip artifact |

Measured language never overwrites a bronze declared-language hint. Aligned or
cleaned subtitles never overwrite captured tracks. Order remains explicit in
the graph: trim-then-Demucs is not Demucs-then-trim.

## Recovery

Losing the orchestration database may lose convenient run history, but it must
not make committed artifacts unreadable. Generic envelopes and published
bundles provide media recovery evidence. PostgreSQL backups plus immutable
Parquet ledger snapshots preserve episode decisions. Consumer services require
stable gold artifact/bundle contracts, not either internal database.

## Acceptance

- one adapter supports bronze references and at least AAC/SRT outputs;
- no large media crosses the control-plane API/database;
- a retry adopts committed content and ignores partial uploads;
- stored artifacts remain intelligible without orchestrator run IDs;
- adding a second transformation does not require a new service endpoint;
- checks and human metadata are visible in the candidate UI.
