# Bronze media, episode identity, and publication

Status: Dagster proof of value in progress. PostgreSQL ledger design approved;
table implementation remains gated after external bronze registration.

## Recommendation

Use Dagster to orchestrate compilation from immutable bronze evidence into
silver claims and gold bundles. Use the shared PostgreSQL server for the live,
indexed episode-identity ledger. Keep media and versioned open snapshots in
Garage.

```text
Garage bronze manifests/media
  -> external bronze_capture[capture ID]
  -> PostgreSQL episode hints and binding decisions
  -> validated episode_binding[anilist-{id}/e{episode}]
  -> silver media artifacts in Garage
  -> versioned gold episode bundle
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

## Current proof boundary

The next gates are deliberately small:

1. Represent committed bronze captures as external Dagster assets. A repair
   sensor registers capture partitions and reports manifest ETags as data
   versions; it never launches a job that pretends to create bronze.
2. Add the PostgreSQL schema and repository boundary described in
   `03-partitions-identity-and-binding.md`.
3. Produce capture-keyed episode hints and accept one binding transactionally.
4. Quarantine one conflicting mapping and expose the result in Dagster.
5. Change a resolver recipe version and rebuild only affected partitions.

Do not begin LID, alignment, portable AAC, or consumer integration until these
gates show that the identity bridge is understandable and operable.

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
