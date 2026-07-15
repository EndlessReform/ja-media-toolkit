# Invalidation, rebuilds, and retention

Status: proposed operational semantics. Destructive automation is explicitly
deferred.

## State vocabulary

Do not reduce every problem to `valid=false`.

| State | Meaning | Normal response |
| --- | --- | --- |
| Missing | Never materialized or physically absent | Build or repair |
| Failed | Attempt did not commit a valid result | Retry after diagnosis |
| Stale/unsynced | Inputs, definition, model, or policy changed | Rebuild selected records/artifacts |
| Superseded | A newer binding/result replaced it | Preserve for history; do not select as current |
| Quarantined/rejected | Known unfit for a policy | Block consumers/downstream work |
| Deleted | Bytes intentionally reaped | Preserve tombstone/metadata where useful |

Historical reproducibility and current acceptability are different properties.

## How staleness works

The orchestrator only knows what is declared and observed:

- code/model/recipe versions are explicit;
- upstream materializations expose data versions;
- external bronze changes must be observed or notified;
- dependencies must be declared rather than hidden S3 reads.

Dagster records code and collection materialization versions, but PostgreSQL
and artifact manifests own item-level staleness. A result is current only when
its complete input fingerprint and recipe/model version match. Collection
updates query for missing or stale records and map tasks over that bounded
selection. External storage mutation remains invisible until observed.

## LID failure example

Suppose `audio-lid-v1` incorrectly accepted dubbed audio for two series.

1. Publish `audio-lid-v2` including model/checkpoint/config identity.
2. Launch the LID collection update with the affected AniList IDs as a semantic
   selector.
3. Repository code selects only rows missing `audio-lid-v2` for the exact input
   audio version.
4. Record policy decisions for non-Japanese audio.
5. Rebuild downstream assets whose input data version changed.
6. Publish new bundles/dataset versions.
7. Mark old datasets stale or quarantined, but retain their pinned inputs until
   lifecycle policy permits removal.

The orchestrator manages the collection update, mapped tasks, retries, logs,
history, and coarse backfills. Domain code and PostgreSQL identify the affected
records and define the Japanese-audio threshold.

## Manual quarantine

Some corrections are judgments, not code changes. Represent them as explicit
versioned decision/check inputs rather than deleting history:

```text
episode_acceptance[anilist-15451/e003]
dataset_eligibility[anilist-15451/e003, policy=voice-research-v1]
```

A consumer bundle requires passing decisions/checks. Existing bytes remain
available for audit unless explicitly reaped.

## Rebuild selection

At minimum support semantic work selectors for:

- one locator, capture, pair, or result reference;
- all episodes in one AniList series;
- missing or stale records for one collection asset;
- operationally failed work and domain review issues;
- a collection asset plus selected downstream products;
- all members of one pinned dataset;
- explicit force-rebuild despite an unchanged fingerprint.

Users select by stable domain locators and human-friendly metadata. Internal
binding/result IDs are resolved by repository code and are not copied manually
between scripts.

## S3 lifecycle tiers

### Retain indefinitely by default

- committed bronze;
- current accepted bindings/bundles;
- artifact versions referenced by active publications;
- artifact versions referenced by pinned datasets;
- explicit retain markers.

### Short automatic lifetime

Use Garage/S3 lifecycle rules for prefixes that are never canonical:

```text
tmp/                     7 days
uncommitted/             7 days
failed-run-scratch/      14 days
ephemeral-export-cache/  30–90 days
```

### Silver garbage-collection candidates

Old immutable silver versions may be candidates only if they are unreferenced
and older than a grace period.

## Conservative mark-and-sweep

One generic GC command is sufficient:

1. roots are current bundles, publications, pinned datasets, and retain markers;
2. follow artifact/bundle input references backward;
3. mark every reachable immutable object/version;
4. report unreferenced candidates older than the grace period;
5. verify they are not active materializations or current pointers;
6. delete only after explicit confirmation;
7. write a deletion report/tombstone.

Initial interface:

```text
ja-media media gc --dry-run
ja-media media gc --candidate-report report.json
```

No automatic semantic deletion is authorized until dry-run reports have been
reviewed over time.

Dagster does not automatically coordinate underlying partition deletion with
its metadata; its own documentation recommends storage deletion plus
synchronization/retention logic. This is why Garage lifecycle and media GC stay
explicit: [Dagster partition retention](https://docs.dagster.io/guides/build/partitions-and-backfills/data-retention).

## Orchestrator metadata retention

Run logs, schedule/sensor ticks, and orchestration event history have separate
retention from media artifacts. Pruning them must never imply S3 deletion.
Likewise, deleting an S3 export must not silently rewrite past run history.

## Dataset lifecycle

Dataset manifests are small and retained. Large shard exports are immutable but
rebuildable:

- pinned/referenced export: retain;
- unpinned export: short-to-medium lifecycle;
- new input/result: new dataset/export version, never in-place shard patching;
- known bad input: mark dataset stale/quarantined and issue a new version.

## Acceptance

- model/config changes identify selected stale records/artifacts;
- blocking LID prevents new downstream consumer bundles;
- old bundles/datasets remain reproducible and visibly superseded/quarantined;
- a missing Garage object is detected rather than trusted from orchestration
  history;
- GC dry-run proves reachability from publications/datasets;
- no automated rule can delete committed bronze.
