# ja-media data layer

This package owns Dagster definitions and Garage adapters for durable data
products. Domain contracts remain in `packages/core`; this package owns the
orchestration-specific asset, partition, sensor, and execution vocabulary.

Bronze captures are external assets because ingest commits them before Dagster
sees them. They use dynamic partitions keyed by `capture_id`. The scan sensor
lists only `metadata/*.json` commit markers, registers unseen capture IDs, and
reports an external materialization keyed by capture ID plus S3 ETag. An
unchanged repair scan is idempotent; a changed manifest appears as a new data
version. Large media bytes never pass through Dagster.

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
buckets. The scan run limit bounds bootstrap pressure; additional captures are
requested on later sensor ticks.

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
