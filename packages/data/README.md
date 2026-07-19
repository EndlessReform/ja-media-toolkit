# ja-media data products and operator workbench

This package compiles immutable Garage captures into versioned DuckLake
products. Dagster owns the asset graph and execution history; FastAPI owns the
domain-specific operator view and transactional human decisions.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing product, campaign,
orchestration, worker, or workbench boundaries.

## Supported operator stack

Start the local data and Dagster overlay from the repository root:

```sh
docker compose \
  -f deploy/lakehouse-dev/compose.yaml \
  -f deploy/lakehouse-dev/compose.dagster.yaml \
  up -d --build --wait \
  postgres minio minio-init rabbitmq dagster-schema-init \
  dagster dagster-daemon dagster-server-worker
```

The Dagster processes use host networking locally so the configured bronze
Garage endpoint can use host tailnet routing. Set
`JA_MEDIA_BRONZE_S3_ENDPOINT_URL` in
`packages/data/.env` using its full `device.<tailnet>.ts.net` MagicDNS name;
also set `JA_MEDIA_SERVICES_ROOT_URL` to the full gateway name. Code locations
never mount the operator's personal `config.toml`. macOS search suffixes do not
propagate into containers. Missing configuration is an error. Docker Desktop
users must enable host networking first. See [DAGSTER.md](DAGSTER.md) for the
copy-paste workbench environment and acceptance sequence.

Apply additive DuckLake and PostgreSQL application schemas, then start the
loopback workbench using the local environment block in
[DAGSTER.md](DAGSTER.md). The explicit block prevents a shared catalog setting
from being accidentally mixed with the disposable local Dagster instance.

Open:

- operator workbench: <http://127.0.0.1:8765/operator>
- Dagster execution detail: <http://127.0.0.1:53000>

`ja-data` merges `.env` files from the repository root down to the invocation
directory. More-specific files win, while variables explicitly exported by
the launching process win over every file.

The workbench is read-only in E2.1. It shows current domain products, binding
candidates, stage-owned exception tables, Dagster-derived campaign spines, and
curated Dagster run history. Raw logs, retries, cancellation, and asset-event
inspection remain in Dagster. Product and exception rows remain in DuckLake;
the UI never reconstructs them from Dagster events.

For an existing shared catalog whose migrations are operator-owned, use
`uv run ja-data web --no-schema-init`. This still creates the connection pool
and Dagster gateway at startup but performs no schema writes.

## Run the canonicalization campaign

Until the operator launch gate is implemented, launch through Dagster:

```sh
docker compose \
  -f deploy/lakehouse-dev/compose.yaml \
  -f deploy/lakehouse-dev/compose.dagster.yaml \
  exec -T dagster \
  dagster job execute \
  --module-name ja_media_data.orchestration.dagster.definitions \
  --job canonicalization_campaign
```

Refresh the workbench after the run. `Run #N` is Dagster's monotonic public run
record ID; the disclosed UUID is the stable cross-system identity. A product
may retain an older successful head when a later run fails. The UI deliberately
shows product lineage and latest execution as separate facts.

The former `ja-data run`, `targets`, `plan`, and recipe-registry commands were
removed in E2.1. They drove a second planner and execution ledger and are not a
compatibility surface. `resolve-sample` remains a non-publishing live canary;
it no longer has an `--apply` path.

## Remaining data commands

```sh
uv run ja-data scan-bronze --limit 100
uv run ja-data resolve-sample --limit 100 --show issues
uv run ja-data resolution-report --limit 100
uv run ja-data campaigns
uv run ja-data campaign canonicalization-gate --series 10087
uv run ja-data bind anilist 10087 14 --capture <capture-id>
uv run ja-data bind anilist 10087 14 --unbind
```

The DuckLake data prefix defaults to `audio/anime/lakehouse`. Override the
complete path with `JA_MEDIA_DUCKLAKE_DATA_PATH`, or only the prefix with
`JA_MEDIA_DUCKLAKE_DATA_PREFIX`.

## Worker boundary

`workers/contracts.py` defines the versioned item envelope. Celery carries the
small descriptor; Garage carries media. An environment command receives a
temporary local request file and scoped Garage credentials. It never receives
DuckLake/control PostgreSQL credentials or a permanent per-item request object.
The E1-B native-worker proof remains documented in [DAGSTER.md](DAGSTER.md);
promotion into the supported worker CLI is E2.2.

## Tests

The suite uses the disposable PostgreSQL fixture in `deploy/lakehouse-dev`:

```sh
uv run --directory packages/data pytest tests
```

Set `JA_MEDIA_PHASE_B_MINIO_SMOKE=1` to additionally exercise Parquet
round-trips through disposable MinIO.
