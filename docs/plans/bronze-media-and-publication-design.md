# Bronze media, derived assets, and publication

Status: revised design and implementation plan for review. This document
authorizes no code, deployment, or remote-infrastructure changes.

This plan began as "add an S3 backend to derived audio." The design discussion
exposed a larger but recognizable problem: the toolkit is becoming a small ML
data-production system. It needs to retain raw evidence, resolve uncertain
identity, materialize reusable transformations, invalidate bad results, build
consumer-specific datasets, and explain all of that later.

We should own the Japanese-media semantics. We should not casually build our
own workflow registry, lineage UI, stale detector, backfill engine, and worker
dispatcher.

## Current recommendation

Proceed with the bronze contract and read path. Before implementing silver,
timebox one weekend for a real orchestration spike comparing Dagster and
Prefect against the same small workflow. This is a disposable evaluation, not
an invitation to productionize both systems. If the evidence is still muddy at
the end of the weekend, choose the simpler fallback and keep moving.

Dagster is the leading technical candidate because its asset, partition,
version, check, and backfill model closely matches the problem. Prefect remains
the leading operational alternative because intermittently available Mac,
CUDA, Proxmox, and cloud workers fit its pull-worker model more naturally.

Neither tool owns the consumer-facing meaning of an episode. The durable handoff
is a versioned `EpisodeBundle` that selects exact audio and subtitle artifacts
under a named policy. Audio, subtitle, Audiobookshelf, and dataset consumers
read that contract rather than querying orchestrator internals.

## The target shape

```text
source media
  -> immutable bronze capture
  -> capture-keyed evidence and inference
  -> accepted episode binding
  -> episode-keyed derived assets and checks
  -> versioned episode bundle
  -> publication or pinned dataset
  -> optional WebDataset export
```

The planes remain separate:

| Plane | Owns |
| --- | --- |
| Domain | Capture, track, episode binding, artifact reference, bundle, publication, and dataset contracts |
| Orchestration | Transformation definitions, declared lineage, runs, checks, partitions, retries, and backfills |
| Storage | Garage objects, immutable artifact versions, commit markers, and lifecycle rules |
| Compute | Local processes, Mac/MLX, CUDA, Proxmox guests, or ephemeral cloud jobs |
| Application | Stable audio/subtitle discovery, streaming, completeness, and publication APIs |

## Decisions that no longer need debate

1. Bronze records captured evidence and supplied series context. It does not
   assert episode identity, measured language, or fitness for a consumer.
2. Before episode binding, partitions and results are keyed by capture or track
   ID. After an accepted binding, consumer-oriented assets may be keyed by
   `anilist-{id}/e{episode}`.
3. Raw bronze audio is served as a capture. It cannot honestly use the current
   episode/profile derived-audio route.
4. Transformations are registered as orchestrator asset/flow definitions if a
   candidate passes the spike. We do not build a general `operations` registry
   first.
5. Storage placement is controlled by a Garage adapter/I/O manager. The
   orchestrator records versions and metadata; Garage remains the byte store.
6. Applications never traverse the orchestration database. They consume a
   stable episode bundle or result reference.
7. "Invalid" is not one state. Missing, failed, stale, superseded, quarantined,
   and physically deleted remain distinct.
8. WebDataset shards are disposable exports of a pinned dataset, not the
   canonical silver representation.

## Documents

- [Ownership and vocabulary](bronze-media-and-publication/00-ownership-and-vocabulary.md)
- [Bronze contract and access](bronze-media-and-publication/01-bronze-contract-and-access.md)
- [Orchestration options and spike](bronze-media-and-publication/02-orchestration-options-and-spike.md)
- [Partitions, identity, and episode binding](bronze-media-and-publication/03-partitions-identity-and-binding.md)
- [Silver assets and storage](bronze-media-and-publication/04-silver-assets-and-storage.md)
- [Invalidation, rebuilds, and retention](bronze-media-and-publication/05-invalidation-rebuilds-and-retention.md)
- [Consumer services and episode bundles](bronze-media-and-publication/06-consumer-services-and-bundles.md)
- [Capture-first ingest and publication](bronze-media-and-publication/07-ingest-and-publication.md)
- [Packed dataset exports](bronze-media-and-publication/08-packed-datasets.md)
- [Phased implementation roadmap](bronze-media-and-publication/09-implementation-roadmap.md)

## Immediate stop/go boundary

Implementation above bronze does not begin until the spike demonstrates:

- a bronze capture observed as a source partition;
- capture-keyed episode inference;
- an accepted binding that creates an episode-keyed partition;
- LID with one passing and one blocking result;
- one derived audio asset stored in Garage;
- a versioned `display-v1` episode bundle;
- a consumer reading that bundle without querying the orchestrator database;
- a code/model-version change identifying affected work;
- a targeted rebuild across multiple episode partitions;
- control plane in Proxmox with work executed on a separate machine or external
  job system;
- acceptable behavior while that compute machine is offline.

If neither candidate makes this workflow understandable and operable, fall back
to DVC/Snakemake plus a small domain catalog. Do not proceed by reconstructing
Dagster badly inside `packages/core`.

## Existing architectural failure

`envs/services/src/ja_media_services/kitsunekko_subtitles/app.py` remains a
717-line hand-written hard-limit violation. Any subtitle implementation phase
must split it before adding behavior. `ja_media_core/kitsunekko.py` is also
above the 300-line soft limit and should be split while introducing neutral
subtitle contracts.
