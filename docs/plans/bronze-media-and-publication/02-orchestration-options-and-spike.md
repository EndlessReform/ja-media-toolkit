# Dagster proof of value

Status: Phase 3 evidence collected. Dagster remains a candidate for
collection-level orchestration; per-record software-defined assets are not the
approved downstream model.

## Question tested

Can Dagster make the bridge from captured evidence to accepted episode
identity visible, rebuildable, and understandable while PostgreSQL and Garage
retain the durable domain data?

The first implementation tried to express that bridge as:

```text
bronze_capture[capture ID]
  -> episode_resolution[capture ID]
  -> validated_episode_mapping[capture ID]  # optional output
  -> future episode_binding[locator]
```

## What the proof established

- External capture observation is a good use of dynamic capture partitions.
  The capture ID is stable evidence identity, and one bad manifest can be
  inspected without treating Dagster as its creator.
- Capture resolution is deterministic and idempotent when the input ETag,
  recipe version, method, and candidate locator form the claim identity.
- PostgreSQL correctly owns accepted bindings, uniqueness, current heads, and
  review issues.
- The bounded 100-capture slice produced 52 accepted bindings and 48 durable
  issues. These were meaningful domain outcomes, not infrastructure failures.
- Dagster run logs and selected-partition execution were useful during the
  spike.

## What the proof rejected

`validated_episode_mapping` used `output_required=False`: accepted captures
materialized it and quarantined captures did not. This is safe for downstream
data, but it gives the Dagster partition view two meanings for "missing": not
processed and processed-but-rejected.

The proposed repair—maintaining a second dynamic partition registry containing
only accepted locator keys—would duplicate PostgreSQL's authoritative
eligibility state. The capture-to-locator relationship is data-dependent and
many-to-one, so it is not an ordinary structural partition mapping. Repeating
that pattern for selected media pairs, alignments, and clips would make the
orchestrator another domain state machine.

Do not implement that repair.

## Revised model under evaluation

Use a small asset graph whose nodes are durable collections:

```text
bronze_media_inventory
  -> episode_identity_ledger
  -> stable_episode_inventory
  -> portable_audio / audio_lid / subtitle_inventory
  -> selected_media_pairs
  -> subtitle_alignments
  -> episode_bundles / dialogue_clip_dataset
```

Each executable asset may contain a task graph:

```text
select a bounded eligible set from PostgreSQL
  -> map typed task over capture/episode/pair references
  -> retry operational failures
  -> commit idempotent rows and immutable artifacts
  -> materialize the updated durable collection with counts
```

PostgreSQL answers which records are eligible, missing, stale, accepted, or in
review. Dagster answers which collection update ran, which tasks retried or
failed, which upstream products it used, and what durable collection was
updated. Garage stores media and open snapshots.

## Process and storage locations

| Component | Execution/storage location |
| --- | --- |
| Webserver and daemon | Always-on control-plane VM |
| Code location | Container on that VM; stateless except logs/scratch |
| Dagster metadata | Dedicated `dagster` database |
| Domain/work ledger | Dedicated `ja_media_data` database on flash-backed PostgreSQL storage |
| Bronze, artifacts, snapshots | Garage; bulk capacity may be HDD-backed |
| Heavy transformations | Separate Mac, CUDA host, guest, or external job |

Workers receive typed capture, locator, pair, and artifact references. They do
not maintain private lookup databases or pass media through the control plane.

## Next acceptance test

Reshape the identity code as one collection-level `episode_identity_ledger`
asset with internal bounded mapping. Then build the first real
`portable_audio` plus `audio_lid` slice.

Continue with Dagster only if the result makes it easy to:

- launch work without copying opaque IDs between scripts;
- see which typed items were selected and why;
- retry an operational failure without recomputing committed successes;
- distinguish domain review outcomes from failed infrastructure;
- invalidate results by input and recipe fingerprint;
- understand collection-level lineage in the UI; and
- recover all domain meaning without Dagster's internal database.

If Dagster adds little beyond launching a PostgreSQL-backed worker, compare the
same slice with Prefect before approving it as the long-term orchestrator.
