# Bronze media, episode identity, and publication

Status: Phases 1-3 were implemented as a Dagster proof of value. The
PostgreSQL ledger and external bronze registration remain approved. The
capture-to-locator partition-promotion design is rejected and must be removed
before the first downstream silver vertical slice.

## Recommendation

Continue evaluating Dagster as the orchestrator for a small graph of durable
collection-level products. Use Dagster tasks inside those products for bounded
per-capture, per-episode, or per-pair work. PostgreSQL, not Dagster partitions,
owns row-level eligibility, fingerprints, current decisions, and review state.
Keep media and versioned open snapshots in Garage.

```text
Garage bronze manifests/media
  -> external bronze inventory
  -> episode identity ledger (PostgreSQL collection)
  -> stable episode inventory (PostgreSQL collection keyed by locator)
  -> silver artifact collections in Garage plus PostgreSQL catalogs
  -> versioned gold bundle collections
  -> application or publication
```

The PostgreSQL service runs on the always-on control-plane/server VM. Its data
volume must be flash-backed. It is not a file inside a Dagster container, on a
developer laptop, or on the HDD-backed Garage volume.

## Storage ownership

| Location | Owns |
| --- | --- |
| Garage bronze prefix | Immutable captured media, tracks, manifests, and provenance |
| `ja_media_data` PostgreSQL database | Capture index, hints, bindings, conflicts, and review state |
| `dagster` PostgreSQL database | Dagster runs, events, sensors, checks, and partition history only |
| Garage silver/gold prefixes | Immutable derived artifacts, snapshots, and published bundles |
| Worker scratch | Downloads and temporary outputs; never durable identity |

PostgreSQL and Dagster use separate databases and principals. Consumer services
must not query Dagster's database. Normal applications eventually consume gold
bundle contracts rather than the internal silver ledger.

## Result of the first proof

The completed proof established:

1. Committed bronze captures can be observed as external Dagster assets. A repair
   sensor registers capture partitions and reports manifest ETags as data
   versions; it never launches a job that pretends to create bronze.
2. The PostgreSQL schema and repository boundary described in
   `03-partitions-identity-and-binding.md`.
3. Capture-keyed resolution can produce idempotent hints, accepted bindings,
   and durable review issues.
4. A 100-capture development slice accepted 52 bindings and quarantined 48
   with useful reasons.

It also disproved the proposed `validated_episode_mapping` boundary. Giving a
capture-partitioned downstream asset an optional output makes rejected domain
outcomes look missing, while registering a second locator partition namespace
duplicates PostgreSQL state and complicates data-dependent fan-in. Do not
continue that model.

## Next proof boundary

1. Remove `validated_episode_mapping` and its `stable_episode_mapping` asset
   check from the executable graph. Preserve the resolver, ledger, diagnostics,
   and tests of domain policy.
2. Define one collection-level `episode_identity_ledger` asset whose internal
   task graph selects a bounded set of unresolved or stale captures, maps the
   resolver over them, and commits bindings/issues idempotently.
3. Expose `stable_episode_inventory` as a PostgreSQL-backed durable collection,
   not a dynamically synchronized locator partition registry.
4. Prove task-level retry behavior: committed successes are adopted, an
   operational failure can be retried without recomputing them, and domain
   ambiguity remains review state rather than a failed run.
5. Use the first real `portable_audio` plus `audio_lid` slice to decide whether
   Dagster's run UI, task retries, and collection lineage justify keeping it.

Do not add per-locator or per-clip Dagster partitions as part of this proof.

## Durable identity rules

- Bronze is keyed by capture or track ID and makes no episode claim.
- Hints are immutable, versioned claims about a capture.
- An accepted binding creates the logical locator
  `anilist-{id}/e{episode}`.
- Binding history is immutable. Correction writes a new binding and updates a
  transactional current-head projection.
- Multiple accepted heads are a conflict; no latest-wins rule is allowed.
- Orchestrator run IDs may be retained as diagnostics but never become domain
  identity.
- PostgreSQL ledger tables are exported to versioned Parquet snapshots in
  Garage for recovery, analysis, and orchestrator replaceability.

## Detailed contracts

- [Ownership and vocabulary](bronze-media-and-publication/00-ownership-and-vocabulary.md)
- [Bronze contract and access](bronze-media-and-publication/01-bronze-contract-and-access.md)
- [Dagster proof](bronze-media-and-publication/02-orchestration-options-and-spike.md)
- [Partitions and PostgreSQL schemas](bronze-media-and-publication/03-partitions-identity-and-binding.md)
- [Silver assets and storage](bronze-media-and-publication/04-silver-assets-and-storage.md)
- [Invalidation and retention](bronze-media-and-publication/05-invalidation-rebuilds-and-retention.md)
- [Consumer bundles](bronze-media-and-publication/06-consumer-services-and-bundles.md)
- [Ingest and publication](bronze-media-and-publication/07-ingest-and-publication.md)
- [Packed datasets](bronze-media-and-publication/08-packed-datasets.md)
- [Implementation roadmap](bronze-media-and-publication/09-implementation-roadmap.md)
- [Phase 3 spike report](bronze-media-and-publication/10-phase3-spike-report.md)
