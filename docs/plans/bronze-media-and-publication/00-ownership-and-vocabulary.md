# Ownership and vocabulary

Status: terminology updated after the Phase 3 proof. Storage ownership is
approved; the orchestrator remains under evaluation.

This document exists because "asset," "result," "locator," "partition," and
"episode" were being used for several different ideas.

## What problem this is

This is dataset engineering: turning messy media evidence into reproducible,
quality-gated inputs for several consumers. The general system resembles a data
build tool or compiler:

| Compiler/build concept | Media/ML concept |
| --- | --- |
| Source file | Bronze capture |
| Symbol resolution | Capture/track to episode binding |
| Type or validity check | LID, alignment, completeness, and quarantine policy |
| Intermediate representation | Normalized cues, AAC, stems, and clips |
| Incremental build graph | Asset lineage and versions |
| Lockfile | Pinned dataset manifest |
| Package | WebDataset shard set |

The system is not "a tiny Airflow." Airflow-like scheduling is only one slice.

## Durable domain terms

### Capture ID

Stable identity for one committed bronze extraction. It makes no episode claim.

```text
capture-01J...
```

### Track ID

Stable identity for one captured or provider subtitle/audio track. It is scoped
by provider where required.

### Logical episode locator

Human/application identity:

```text
anilist-15451/e003
```

It is mutable knowledge. A locator may resolve to different evidence after a
correction.

### Artifact reference

Immutable reference to produced bytes:

```text
result ID, object key, media type, size, hash, data version
```

Application code should pass lightweight references rather than large audio
objects through the orchestrator.

### Episode binding

An explicit, versioned assertion connecting one logical episode locator to
capture and track evidence. Before this boundary, work is keyed by capture or
track IDs. After it, episode-oriented assets may use the logical locator.

### Episode bundle

Consumer-facing selection of exact artifacts under a named policy:

```text
display-v1
audiobookshelf-v1
voice-research-v1
```

It is the stable handoff between the data-production graph and application
services.

### Publication

Materialized consumer projection, such as an Audiobookshelf directory. A
publication references bundle/artifact versions and may be rebuilt.

### Dataset manifest

Immutable selection of exact bundle/result IDs, splits, exclusions, and
completeness rules for one research use.

## Orchestrator terms

### Asset definition

A named durable data product, usually a collection backed by a PostgreSQL
relation, an artifact catalog/prefix, or a published manifest family:

```text
bronze_media_inventory
episode_identity_ledger
stable_episode_inventory
audio_language_results
subtitle_alignments
dialogue_clip_dataset
```

It is not one row, one task invocation, or a transient list of IDs. A large
asset may use a graph of mapped tasks to update many independently fingerprinted
rows or artifacts.

### Partition

One independently tracked slice of an asset when that slice has a stable,
operationally useful meaning:

```text
bronze_capture[capture-01J...]
ledger_snapshot[2026-07-14T00:00:00Z]
```

A partition is not necessarily an S3 prefix, DB partition, shard, or worker.
Do not create a Dagster partition merely because a PostgreSQL table has a row.
In particular, do not dynamically mirror every accepted locator, media pair,
or clip into Dagster unless independent UI selection/backfill is a measured
requirement worth the synchronization cost.

### Materialization

One recorded production of an asset partition at a data/code version. Its bytes
may live in Garage; the orchestration control plane records the event, metadata,
checks, lineage, and run logs.

For an external asset such as `bronze_capture`, the event means Dagster observed
a materialization produced by ingest. Dagster cannot execute that source asset.

### Asset check

Versioned validation such as audio language, referenced-object presence, or
alignment quality. A blocking check may prevent downstream materialization.

Checks validate a durable collection or a meaningful partition. Row-level
eligibility and human-review status belong in typed domain results and the
PostgreSQL ledger; they are not represented by omitting an asset
materialization.

### Task

One execution step inside an asset update, such as parsing a capture, invoking
LID, running ALASS, hashing an output, or committing an artifact. Tasks may map
over a bounded PostgreSQL selection and retry independently. Their parameters
are typed domain references, not IDs copied manually between scripts.

## Plane ownership

### `packages/core`

Owns durable Japanese-media contracts and lightweight clients:

```text
BronzeCapture
BronzeTrack
EpisodeHint
EpisodeBinding
MediaArtifactRef
EpisodeBundle
Publication
DatasetManifest
```

It does not own a generic workflow registry, task scheduler, backfill planner,
or run-history database.

### Orchestrator

Owns definitions and operational state:

- declared asset/flow graph;
- code/data versions at durable product boundaries;
- runs, mapped-task retries, logs, checks, and materializations;
- coarse partition status and backfills where partitions are useful;
- dispatch to compute.

It does not decide episode semantics or provide the stable media API.

### PostgreSQL domain ledger

The `ja_media_data` database owns the live, indexed capture-to-episode decision
state:

- normalized capture headers;
- immutable hints and binding decisions;
- the transactionally current binding projection;
- conflicts and human review state.

It runs on the shared always-on PostgreSQL service with flash-backed storage.
It is separate from Dagster's operational database. Versioned Parquet exports
in Garage preserve an open analytical and recovery representation.

### Garage

Owns media bytes, immutable artifact versions, commit markers, ledger snapshots,
and coarse prefix lifecycle rules. It is not the point-lookup path for normal
episode resolution.

### Compute

Owns actual execution. The control plane may launch a local process, Docker/ECS
task, custom job, or lightweight submitter to an external batch system. The
control plane VM is not expected to run Demucs or Encodec.

### Consumer services

Own stable discovery/streaming contracts. They read published bundles and
artifact references, not orchestrator event tables.

## What must remain replaceable

Orchestrator run IDs, worker IDs, and internal database primary keys never
become durable artifact identity. A stored artifact may record them as optional
diagnostics, but deleting or replacing the orchestrator must not make Garage
objects or published bundles unintelligible.
