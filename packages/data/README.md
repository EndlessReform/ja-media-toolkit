# ja-media data layer

This package owns Dagster definitions and Garage adapters for durable data
products. Domain contracts remain in `packages/core`; this package owns the
orchestration-specific asset, partition, sensor, and execution vocabulary.

Bronze captures are external assets because ingest commits them before Dagster
sees them. They use dynamic partitions keyed by `capture_id`. The scan sensor
lists only `metadata/*.json` commit markers, registers unseen capture IDs, and
reports an external materialization keyed by capture ID plus S3 ETag. An
unchanged repair scan is idempotent; a changed manifest appears as a new data
version. The same sensor upserts the small manifest header into the shared
PostgreSQL capture index. Large media bytes never pass through Dagster or
PostgreSQL.

## Configuration

Keep the data code location's credentials and settings in its own `.env`:

```sh
cd packages/data
cp .env.example .env
$EDITOR .env
```

Run Dagster commands from `packages/data`; Dagster then loads this package-local
`.env` into the webserver, daemon, and code-location subprocesses. Shell command
prefixes and manually exported variables are not required. Existing shell
variables still take precedence when an intentional one-off override is useful.

AWS credentials use boto3's standard variable names. Only the credentials and
`JA_MEDIA_BRONZE_BUCKET` lack useful code defaults. The bucket is deliberately
not discovered because a Garage principal need not enumerate unrelated
buckets. `JA_MEDIA_DATA_DATABASE_URL` selects the separate domain ledger;
standard `postgresql://` and explicit `postgresql+psycopg://` URLs are accepted.
Percent-encode reserved characters in its password component. The scan event
limit bounds bootstrap pressure; additional captures are reported on later
sensor ticks.

## Database schema

Alembic owns the ledger schema. Review generated SQL before applying the
additive migration to development:

```sh
cd packages/data
set -a
source .env
set +a
uv run alembic upgrade head --sql
uv run alembic upgrade head
uv run alembic check
```

The initial migration creates `bronze_captures`, `episode_hints`,
`episode_bindings`, `current_episode_bindings`, and
`episode_resolution_issues`. Its downgrade deliberately refuses to drop the
ledger tables. Production migration remains a separate user-owned operation.

The ordinary test suite uses SQLite for fast transaction checks. The opt-in
integration test requires a disposable local PostgreSQL database whose name
ends in `_test`:

```sh
JA_MEDIA_DATA_TEST_DATABASE_URL='postgresql+psycopg://user:password@127.0.0.1:5432/ja_media_data_test' \
  uv run pytest tests/test_repository_postgres.py
```

Apply the Alembic migration to that database first. The test refuses non-local
hosts and non-test database names, writes uniquely named rows, and removes them
afterward.

## Episode-resolution probe

The Phase 3 resolver combines the shared core PTN wrapper with an independent
explicit episode token, exact AniList title/synonym agreement, AniList episode
bounds, and PostgreSQL uniqueness. Dry-run and inspect before applying:

```sh
cd packages/data
set -a
source .env
set +a
uv run ja-media-data resolve-sample --limit 100 --show issues
uv run ja-media-data resolve-sample --limit 100 --apply --show none
uv run ja-media-data resolution-report --limit 100
```

The spike currently still defines `episode_resolution`, its
`stable_episode_mapping` check, and optional `validated_episode_mapping` output.
Do not extend this graph: the optional output made processed-and-quarantined
captures appear missing, and the design is scheduled for removal in Phase 3b.
The resolver, CLI, PostgreSQL ledger, evidence, and diagnostics remain valid.

The replacement proof will expose a collection-level
`episode_identity_ledger` asset. Its internal task graph will select a bounded
set of unresolved or stale captures from PostgreSQL, map the resolver over
typed work items, commit accepted bindings/issues idempotently, and report
aggregate counts. PostgreSQL remains the item-level cache and review authority;
Dagster is being evaluated for task retries, logs, scheduling, and durable
collection lineage.

## Local proof of value

Validate and inspect the code location:

```sh
cd packages/data
uv run dg check defs
uv run dagster asset list -m ja_media_data.definitions
uv run dagster sensor preview bronze_scan_sensor -m ja_media_data.definitions
```

Run the local UI and daemon with persistent state:

```sh
mkdir -p .dagster-home
export DAGSTER_HOME="$PWD/.dagster-home"
uv run dg dev -m ja_media_data.definitions
```

Enable `bronze_scan_sensor` in the UI. The first tick registers capture
partitions and records external materializations; it does not launch a bronze
job because Dagster did not create the source data. Later ticks skip manifest
ETags already reported. The sensor is stopped by default so importing the
package never scans Garage.
