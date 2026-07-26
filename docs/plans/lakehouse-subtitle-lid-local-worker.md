# Local subtitle-LID worker proposal

Status: implemented locally; bounded shared-DEV acceptance remains.

## Recommendation

Use subtitle LID to prove the local-worker boundary before VAD or ASR:

```text
canonical_subtitle_inputs
  -> server selects missing/stale rows
  -> RabbitMQ cpu-light queue
  -> local worker runs existing script/FastText classifier
  -> one Garage result marker per subtitle
  -> server verifies markers and merges DuckLake rows
```

The worker is a broker-only Celery consumer started with:

```sh
cd packages/data
uv run ja-data worker doctor --profile local-cpu
uv run ja-data worker start --profile local-cpu
```

It receives bronze-read and DEV-staging credentials, but no DuckLake,
application-PostgreSQL, or Dagster credentials. Dagster remains the only
run/step ledger. Celery is transport only.

LID is a transport canary, not a new service. It is cheap, already implemented,
and exercises the boundary without adding audio decoding or GPU failure modes.

## Measured baseline

The operator application at `http://100.101.141.65:8080` reports one current
canonical materialization:

- Dagster run: `99e40398-bfdf-4b2a-b0d1-5be0f246e12b`
- materialization:
  `materialization-canonical_inputs-587da81bc8fc40158c71c6c8ea34be0f`
- 4,029 captures and 2,577 proposals;
- 1,452 quarantined captures;
- 2,474 canonicalized episode locators; and
- zero stale, unbound, or waiting locators.

The workbench pages 2,474 canonical episode rows from the 2026-07-24 02:09
materialization. A bounded read-only DuckLake export measured 3,929 canonical
subtitle inputs in that same current DEV product.

## Existing pieces to reuse

- `canonical_subtitle_inputs` already contains the subtitle object reference,
  codec, declared language, and input fingerprint.
- `ja_media_core.subtitle_lid` already owns parsing, script analysis, and the
  FastText fallback.
- `workers/contracts.py` already has versioned envelopes and result markers.
- `invoke_environment` already strips control-plane credentials.
- RabbitMQ, the `server` queue, Garage staging, and
  `worker_handoff_items` already exist.

Do not add an HTTP service, generic stage hierarchy, worker database, heartbeat
table, or second planner.

## Ownership

### Server

One retryable Dagster step on the existing `server` queue:

1. reads the current canonical head;
2. selects missing/stale subtitles in `subtitle_input_id` order;
3. applies the run `limit` and freezes that exact selection;
4. publishes one item envelope to `cpu-light` per subtitle;
5. waits for result markers;
6. validates marker identity, fingerprints, and metric ranges;
7. merges valid LID rows and compacts handoff facts; and
8. emits the normal Dagster materialization metadata.

The server alone reads DuckLake and publishes product rows.

### Local worker

For each delivered envelope:

1. validate the contract and allowed buckets/prefixes;
2. reuse an existing matching marker if present;
3. download the subtitle object;
4. parse SRT/ASS using the existing core code;
5. run `analyze_subtitle_language`;
6. fingerprint the normalized result; and
7. create the deterministic result marker last.

The worker never selects additional work or interprets the campaign graph.

## Minimal contract change

Add one request and one result variant:

```text
SubtitleLidRequest
  operation = "subtitle_language_id"
  subtitle_input_id
  source: ObjectRef
  codec
  input_fingerprint
  recipe_revision
  recipe_parameters
  staging_bucket
  staging_prefix

SubtitleLidResult
  operation = "subtitle_language_id"
  subtitle_input_id
  language
  reason
  script_metrics
  sampled_metrics | null
  input_fingerprint
  recipe_revision
  output_fingerprint
  elapsed_seconds
```

Keep contract version 1 because the existing VAD envelope does not change.
Use typed metric models rather than unvalidated dictionaries.

For LID, the marker contains the complete result; there is no separate output
object. The deterministic marker key is derived from contract version, request
ID, input fingerprint, and recipe revision.

## Product change

The current LID compiler replaces the whole table. Worker results instead merge
at:

```text
(subtitle_input_id, recipe_revision)
```

This is the natural LID product key and lets retries publish only completed
items. DuckLake snapshots retain history; no artifact ledger is needed.

Eligibility is one bounded query comparing:

- the canonical subtitle input fingerprint;
- the requested recipe/config fingerprint; and
- the current result row, if any.

Different recipe revisions may coexist, but no recipe registry is needed for
this slice. The output fingerprint remains in `worker_handoff_items`; the
existing LID row schema already has the input and recipe identities required
for incremental eligibility.

## Selection and retry

`limit` is Dagster run config, not a permanent partition. Selection is the
ordered missing/stale query against the canonical head visible to the Dagster
step. This inexpensive LID slice does not add a second frozen-selection
artifact; normal Dagster retry reruns eligibility and preserves already merged
item heads.

Each request and marker key is deterministic. Celery uses late
acknowledgement:

- death before marker returns the task to the queue;
- acknowledgement loss after marker reuses the marker;
- duplicate delivery is harmless; and
- a canonical change after selection makes the old result stale through its
  input fingerprint.

Valid items commit even if another item fails. The Dagster step then fails with
a bounded item summary, while successfully published heads remain usable.

## Worker profile

Check in one small capability profile:

```toml
[profiles.local-cpu]
queue = "cpu-light"
operations = ["subtitle_language_id"]
concurrency = 1
staging_bucket = "ja-media-dev"
staging_prefix = "worker-staging/dev"
staging_region = "garage"
```

Keep broker and Garage credentials outside the file. Use separate Garage
principals for bronze read and DEV staging write.

`worker doctor` only needs to prove:

- the profile and locked environment load;
- RabbitMQ is reachable;
- bronze can be read and staging can be written; and
- forbidden database credentials will not enter the item command.

## Failure behavior

- **No worker:** RabbitMQ retains tasks; existing heads do not change.
- **Bad input or marker:** that item does not advance.
- **Some failures:** valid items publish, then the Dagster step fails.
- **Server retry:** rerun eligibility and preserve already merged item heads.
- **Cancellation:** stop publication; temporary markers remain eligible for
  normal E2.2 cleanup.

Protected-run replay and cleanup should use the already approved E2.2 retention
rules. Do not design a second retention system for LID.

## Why this shape

- Keeping LID in the server image would not prove the worker boundary.
- A Dagster worker on the laptop would require Dagster storage credentials.
- One batch task would make retries and poisoned-input handling coarser.
- An LID service would add an API and deployment for a library call.

The only new moving part is the supported Celery item consumer required by the
least-privilege worker boundary.

## Implementation status

Completed locally:

1. versioned LID contracts, eligibility, and incremental merge;
2. checked worker profile plus `doctor` and foreground consumer;
3. dedicated `subtitle_lid_from_canonical` Dagster job;
4. RabbitMQ dispatch, marker verification, handoff compaction, and product
   merge; and
5. a three-item integration seeded from the current DEV canonical product.

Remaining shared-DEV acceptance:

1. run a small selection from the measured canonical head;
2. prove no-worker waiting, worker restart, partial failure, and retry; and
3. then run the full eligible subtitle set.
