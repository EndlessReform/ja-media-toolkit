# Partitions, identity, and episode binding

Status: proposed domain model independent of the orchestrator selection.

## A partition in this system

A partition is one independently tracked instance of an asset/flow output
family. It is not necessarily an object-store prefix, database partition,
WebDataset shard, or compute worker.

```text
bronze_capture[capture-01J...]
episode_hint[capture-01J...]
aligned_subtitles[anilist-15451/e003]
```

The asset name answers "what kind of product?" The partition key answers
"which independently buildable instance?"

## Two identity spaces

### Evidence-keyed work

Before trusted episode identity exists, key work by stable evidence:

```text
bronze_capture[capture ID]
bronze_subtitle[provider + track ID]
episode_hint[capture ID]
subtitle_normalization[track ID]
```

An episode hint is still a result about a capture:

```json
{
  "capture_id": "capture-01J...",
  "candidate": {"namespace": "anilist", "id": "15451", "episode": "3"},
  "method": "filename-hints-v2",
  "confidence": 0.94
}
```

It does not rename or mutate bronze.

### Episode-keyed work

An accepted binding creates a logical locator:

```text
anilist-15451/e003
```

The binding contains the evidence selected for that locator:

```json
{
  "schema_version": 1,
  "kind": "episode-binding",
  "binding_id": "binding-01J...",
  "locator": {"namespace": "anilist", "id": "15451", "episode": "3"},
  "audio_capture_id": "capture-01J...",
  "subtitle_track_ids": ["bronze:track-01J..."],
  "evidence_result_ids": ["hint-01J..."],
  "decision": {"method": "human-confirmed", "decided_at": "..."}
}
```

After this boundary, assets intended for episode consumers may share the
logical partition:

```text
episode_audio[anilist-15451/e003]
audio_language[anilist-15451/e003]
aligned_subtitles[anilist-15451/e003]
portable_aac[anilist-15451/e003]
```

## The bridge is explicit

The binding materialization depends on capture-keyed hints/evidence. Downstream
episode assets depend on the binding and follow the capture/track references
stored in it.

An orchestrator can show the asset-family graph automatically once declared.
The exact runtime mapping from `capture-01J...` to `e003` is domain data and must
be emitted as binding metadata. Do not expect the orchestrator to infer it from
S3 paths or filenames.

If the selected framework cannot express data-dependent partition mapping
cleanly, the acceptable fallback is:

- keep exact capture/track inputs in the binding artifact;
- declare asset-level dependency on the binding/evidence family;
- emit exact input IDs and versions in materialization metadata;
- use the binding as the source of truth for downstream loading.

Do not invent a graph database merely to represent this bridge.

## Corrections and conflicts

Bindings are immutable assertions. A correction writes another binding that
supersedes the old one. It does not rewrite bronze or past datasets.

Resolution rules:

- one accepted unsuperseded binding: current;
- multiple accepted heads: explicit conflict, never latest-wins silently;
- no accepted binding: unresolved;
- rejected binding: historical evidence only.

A previous dataset remains reproducible because it pins the binding ID it used,
even if the logical locator later resolves differently.

## Dynamic partition discovery

Initial bronze bootstrap:

1. list committed metadata markers once;
2. parse them through the core bronze contract;
3. register capture IDs as dynamic source partitions;
4. record manifest ETag/hash as the observed source data version;
5. checkpoint listing progress.

Normal expansion should be notification-driven where possible:

```text
bronze writer commits manifest
  -> registers/notifies capture ID
  -> orchestration source partition becomes visible
```

A low-frequency incremental scan using cursor and ETag is the repair path. No
CLI or content request performs a full S3 scan.

Accepted bindings similarly register logical episode partitions. Registering a
partition is metadata-only; downstream materialization happens separately.

## Partition-key design

Use stable machine-readable keys:

```text
capture-01J...
kitsunekko:{file_ref}
anilist-15451/e003
dataset:ja-anime-eval-v1
```

Attach human metadata separately:

```text
series title
episode display label
provider
recipe/model version
content URL
quality summary
```

Avoid putting mutable English titles into partition identity. Avoid one asset
definition per episode. Asset definitions are transformation families; episodes
are partitions.

## Core package boundary

`packages/core` should provide the records and pure validation/resolution rules.
Framework adapters belong in a separate runtime environment/package after the
spike. Importing core must not require Dagster or Prefect.

## Acceptance

- zero/one/multiple-subtitle captures remain valid before episode binding;
- a capture may receive several hints without changing identity;
- one accepted binding creates an episode partition;
- correction creates a new binding and preserves old dataset reproducibility;
- conflicts are visible and block implicit consumer resolution;
- downstream materialization records the exact binding and capture/track input
  versions it used.
