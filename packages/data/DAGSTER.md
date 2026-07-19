# Dagster operations

Dagster is the execution control plane for `packages/data`. This runbook covers
the disposable local overlay and freezes the requirements for the first shared
DEV deployment. Architecture and ownership live in
[ARCHITECTURE.md](ARCHITECTURE.md); this file contains commands and operational
expectations.

## Configuration rules

Use purpose-specific variables. Do not restore a shared S3 endpoint alias and
do not put phase names in durable resources.

```dotenv
# Immutable source captures in Garage.
JA_MEDIA_BRONZE_S3_ENDPOINT_URL=http://storage.<tailnet>.ts.net:3900
JA_MEDIA_BRONZE_BUCKET=ja-media-prod
JA_MEDIA_BRONZE_PREFIX=audio/anime/bronze

# DuckLake products. DEV credentials must be distinct from bronze credentials.
JA_MEDIA_DATA_DATABASE_URL=postgresql://.../ja_media_data_dev
JA_MEDIA_DUCKLAKE_CATALOG_SCHEMA=ja_media_ducklake_dev
JA_MEDIA_DUCKLAKE_DATA_PATH=s3://ja-media-dev/ducklake/dev/
JA_MEDIA_DUCKLAKE_S3_ENDPOINT_URL=http://storage.<tailnet>.ts.net:3900
JA_MEDIA_DUCKLAKE_S3_ACCESS_KEY_ID=...
JA_MEDIA_DUCKLAKE_S3_SECRET_ACCESS_KEY=...
JA_MEDIA_DUCKLAKE_S3_REGION=garage

# Orchestration and first-party services.
DAGSTER_POSTGRES_URL=postgresql://.../ja_media_dagster_dev
JA_MEDIA_CELERY_BROKER_URL=amqp://...@127.0.0.1:5672//
JA_MEDIA_SERVICES_ROOT_URL=http://services.<tailnet>.ts.net
```

Containers receive deployment configuration explicitly. They do not read a
developer's personal config file. Use full tailnet DNS names in container
configuration; relying on a host resolver's search suffix is not portable.
Secrets belong in an untracked env file or the deployment secret manager and
must not appear in Compose YAML or shell history.

## Disposable local stack

Prerequisites are Docker with Compose v2 and host networking support. Docker
Desktop users must enable host networking. The local overlay binds Dagster and
dependency ports only to loopback and uses disposable PostgreSQL/MinIO data.

Put the externally reachable bronze and service endpoints in
`packages/data/.env`, then run from the repository root:

```sh
docker compose \
  -f deploy/lakehouse-dev/compose.yaml \
  -f deploy/lakehouse-dev/compose.dagster.yaml \
  config --quiet

docker compose \
  -f deploy/lakehouse-dev/compose.yaml \
  -f deploy/lakehouse-dev/compose.dagster.yaml \
  up -d --build --wait \
  postgres minio minio-init rabbitmq dagster-schema-init \
  dagster dagster-daemon dagster-server-worker
```

Open Dagster at <http://127.0.0.1:53000>. Check the two external dependencies
from the same network namespace as the code location:

```sh
docker compose \
  -f deploy/lakehouse-dev/compose.yaml \
  -f deploy/lakehouse-dev/compose.dagster.yaml \
  exec -T dagster sh -ec '
    test -n "$JA_MEDIA_BRONZE_S3_ENDPOINT_URL"
    curl --silent --show-error --output /dev/null --connect-timeout 5 \
      "$JA_MEDIA_BRONZE_S3_ENDPOINT_URL"
    test -n "$JA_MEDIA_SERVICES_ROOT_URL"
    curl --fail --silent --show-error --output /dev/null \
      "${JA_MEDIA_SERVICES_ROOT_URL%/}/api/v1/anilist/healthz"
  '
```

An authenticated S3 root may answer 403; that still proves DNS and routing
because the first curl intentionally does not use `--fail`.

Apply schemas and start the workbench from `packages/data`. First load the
configured external read endpoints, then overlay the disposable local DuckLake
destination and Dagster storage. This avoids accidentally pointing the local
workbench at a shared catalog.

```sh
cd packages/data
set -a
source .env
source ../../deploy/lakehouse-dev/local-ducklake.env.example
set +a
export DAGSTER_POSTGRES_URL='postgresql://ja_media_lakehouse_test:ja_media_lakehouse_test@127.0.0.1:55432/ja_media_lakehouse_test?options=-csearch_path%3Dja_media_dagster_local'
uv run ja-data apply-lakehouse-schema
uv run ja-data web
```

Keep that shell open for both commands. The checked-in credentials are valid
only for the loopback disposable stack.

Open <http://127.0.0.1:8765/operator>. For an operator-managed shared catalog,
use `uv run ja-data web --no-schema-init` after migrations have been applied.

Launch canonicalization through Dagster:

```sh
docker compose \
  -f deploy/lakehouse-dev/compose.yaml \
  -f deploy/lakehouse-dev/compose.dagster.yaml \
  exec -T dagster \
  dagster job execute \
  --module-name ja_media_data.orchestration.dagster.definitions \
  --job canonicalization_campaign
```

The operator UI reads product rows from DuckLake and run/step state through
Dagster's public API. A failed rerun does not erase the prior product head.

To inspect without deleting state:

```sh
docker compose \
  -f deploy/lakehouse-dev/compose.yaml \
  -f deploy/lakehouse-dev/compose.dagster.yaml \
  ps
docker compose \
  -f deploy/lakehouse-dev/compose.yaml \
  -f deploy/lakehouse-dev/compose.dagster.yaml \
  logs --tail=100 dagster dagster-daemon dagster-server-worker
```

`down --volumes` is permitted only for this disposable local project after
confirming the Docker context. It is never a DEV or production procedure.

## Shared DEV prerequisites

Assume a Debian VM already has Docker Engine, Compose v2, working tailnet
connectivity, and the required ACLs. Before adding the deployment overlay, the
operator must provide:

1. Two PostgreSQL databases on the existing server:
   `ja_media_data_dev` for DuckLake/application decisions and
   `ja_media_dagster_dev` for Dagster storage. Separate roles are preferred;
   neither runtime should depend on PostgreSQL `search_path` tricks.
2. A dedicated Garage bucket or prefix for DEV DuckLake Parquet and worker
   staging, with credentials that cannot write bronze. The control plane needs
   read/write access; native workers will later receive narrower scoped access.
3. Read-only bronze Garage credentials and the full tailnet endpoint.
4. RabbitMQ durable storage plus three principals: administrator/operator,
   Dagster dispatcher, and native worker. Queue permissions will be narrowed as
   E2.2 freezes capability queue names.
5. A stable tailnet name and ports for Dagster UI and the operator workbench.
   Exposure should remain tailnet-only; no public ingress is required.
6. An untracked deployment env file containing the canonical variables above,
   plus PostgreSQL/RabbitMQ credentials. Do not reuse local test credentials.
7. Backup coverage for both PostgreSQL databases and RabbitMQ definitions.
   Garage product durability follows the existing Garage policy.

No new PostgreSQL server, MinIO deployment, public DNS, TLS termination, or
container registry is required for the first DEV increment. The code location,
server worker, and workbench may initially build from the checkout on the VM.

## First-time DEV setup contract

The deployment overlay implemented in the next pass should make first setup
exactly this shape:

```sh
# 1. Validate configuration without starting anything.
docker compose -f <dev-compose-files> --env-file <untracked-dev-env> config --quiet

# 2. Start only durable low-volatility services.
docker compose -f <dev-compose-files> --env-file <untracked-dev-env> \
  up -d --wait rabbitmq dagster-webserver dagster-daemon

# 3. Run additive schemas once, as an explicit operator action.
docker compose -f <dev-compose-files> --env-file <untracked-dev-env> \
  run --rm schema-init

# 4. Start replaceable application processes.
docker compose -f <dev-compose-files> --env-file <untracked-dev-env> \
  up -d --build code-location server-worker operator-web
```

The final service names may change, but these lifecycle boundaries may not.
Changing Python or templates restarts only replaceable services. Updating a
dependency rebuilds their image. The persistent Dagster webserver/daemon and
RabbitMQ are not rebuilt for application code changes.

First acceptance must prove:

- Dagster loads the code location and persists a run across its restart;
- the code location can read bronze and call the AniList gateway;
- canonicalization publishes the expected live slice to DEV DuckLake;
- the operator UI shows that product and links it to the producing run;
- a failed rerun leaves the preceding product head visible; and
- a queued native step survives with no native worker attached.

## Temporary spike surface

`e1b_delayed_vad`, its `JA_MEDIA_E1B_*` variables, and the phase-E smoke scripts
remain isolated proof code until E2.2 replaces them with the supported worker
CLI and handoff relation. They are not DEV configuration conventions and must
not be copied into the persistent deployment.

Current collection assets are unpartitioned. Capture IDs and episode locators
remain row-level provenance; run-scoped heavy work may fan out by item without
making those IDs permanent Dagster partitions.
