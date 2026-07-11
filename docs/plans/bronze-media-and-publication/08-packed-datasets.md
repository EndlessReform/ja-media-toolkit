# Packed dataset exports

Status: proposed future enhancement after stable bundles/datasets exist.

## Goal

Avoid thousands or millions of small object-store reads when training or
evaluation wants to scan a fixed corpus. Build WebDataset-compatible tar shards
from a pinned dataset manifest while keeping individual artifacts canonical.

## Dataset first

The input is an immutable consumer contract:

```json
{
  "schema_version": 1,
  "kind": "asr-eval-dataset",
  "dataset_id": "ja-anime-eval-v1",
  "samples": [
    {
      "sample_id": "anilist-15451-e003",
      "bundle_id": "bundle-01J...",
      "audio_result_id": "result-01J...",
      "subtitle_result_id": "result-01J...",
      "segments_result_id": "result-01J...",
      "excluded": false
    }
  ]
}
```

Completeness, language, audio profile, segment policy, exclusions, and splits
belong here. Audiobookshelf completeness is irrelevant.

## Shard format

Use ordinary uncompressed POSIX tar with WebDataset-style sample keys:

```text
anilist-15451-e003.audio.m4a
anilist-15451-e003.transcript.srt
anilist-15451-e003.segments.json
anilist-15451-e003.sample.json
```

Uncompressed tar favors sequential reads and byte-range indexing; compressed
audio leaves little obvious gzip benefit. Benchmark seekable compression later.

Suggested layout:

```text
datasets/{dataset_id}/{export_id}/
  shards/000000.tar
  shards/000001.tar
  index.jsonl
  manifest.json
```

The index records sample, shard, members, byte offsets, sizes, and hashes.
`manifest.json` pins the input dataset hash, exporter version, shard policy, and
shard hashes and is written last.

## Orchestration relationship

Model the pinned dataset and shard export as durable assets/flows. Partition by
dataset/export or shard only when that makes rebuild/backfill clearer. Do not
model every tar member as an orchestration asset.

An ephemeral R2/cloud worker receives only pinned shard URLs/keys and hashes. It
does not resolve AniList, bindings, or current bundle pointers.

## Rebuild and lifecycle

Exports are immutable and disposable. Changing one input creates a new dataset
or export version; never patch a tar in place. Pinned exports are GC roots;
unpinned exports may receive a short lifecycle because the source dataset and
artifacts remain rebuildable.

Ordinary media services do not open tar members. Interactive workloads continue
to use artifact/bundle content.

## Benchmark

Compare individual objects with 256 MiB, 1 GiB, and 2 GiB shards for sequential
throughput, cold start, retry cost, cache behavior, and request count using a
representative mix of clips/audio/SRT/JSON.

## Acceptance

- deterministic verified shards from one pinned dataset;
- standard tar and WebDataset reader compatibility;
- sample-to-shard/offset lookup;
- failed builds never publish `manifest.json`;
- measurement justifies the export;
- deleting an export cannot modify its dataset, silver inputs, or bronze.
