# Dagster proof of value

Status: active evaluation. Dagster is the selected candidate for the identity
spike, not yet approved as the final orchestrator for every media workflow.

## Question being tested

Can Dagster make this bridge visible, rebuildable, and understandable?

```text
external capture identity                 accepted episode identity
bronze_capture[capture ID] -> ... -> episode_binding[anilist-{id}/e{episode}]
```

Garage and PostgreSQL retain the durable domain data. Dagster owns execution,
lineage, partition state, checks, retries, logs, and backfills.

## Concrete workflow

```text
bronze_capture[capture ID]       external; manifest already exists in Garage
  -> episode_hint[capture ID]    PostgreSQL rows
  -> binding_candidate[capture ID]
  -> episode_binding[locator]    accepted transaction in PostgreSQL
  -> binding_snapshot            versioned Parquet in Garage
```

A conflicting candidate writes an inspectable resolution issue and does not
create or replace a current binding.

## Process and storage locations

| Component | Execution/storage location |
| --- | --- |
| Webserver and daemon | Always-on control-plane VM |
| Code location | Container on that VM; stateless except logs/scratch |
| Dagster metadata | Dedicated `dagster` database |
| Domain ledger | Dedicated `ja_media_data` database on flash-backed PostgreSQL storage |
| Bronze and snapshots | Garage; bulk capacity may be HDD-backed |
| Heavy transformations | Separate Mac, CUDA host, guest, or external job |

Workers receive capture, binding, and artifact references. They do not maintain
private DuckDB/SQLite copies of the episode lookup table.

## Phase 1: external bronze registration

Define `bronze_capture` as a partitioned external asset. The stopped-by-default
repair sensor:

1. lists only committed `metadata/*.json` markers;
2. derives or reads the stable capture ID;
3. registers unseen dynamic partitions;
4. reports an external materialization for each new capture ID/ETag pair;
5. records the ETag as `dagster/data_version`;
6. records manifest location, size, schema, series hint, and track count as
   event metadata.

There is no `bronze_capture_job`: Dagster did not create the capture.

## Ledger gate

The next phase must show:

- indexed lookup by capture ID and logical episode locator;
- immutable hints with input and recipe versions;
- transactional acceptance of one binding;
- a uniqueness/conflict rule that survives concurrent writers;
- an open issue for ambiguous or overlapping mappings;
- a versioned Parquet snapshot written after the PostgreSQL commit;
- recovery of domain meaning without querying Dagster tables.

## Acceptance

Continue with Dagster only if the UI and code make it easy to answer:

- Which captures have no hint or binding?
- Why did this capture resolve to this episode?
- Which locator or capture is in conflict?
- Which input and recipe version produced the decision?
- What becomes stale after a resolver-version change?
- Can selected partitions be rebuilt without scanning or rebuilding everything?

Compute placement remains a later acceptance item. PostgreSQL persistence does
not prove that intermittently available Mac/CUDA workers are pleasant to run.
