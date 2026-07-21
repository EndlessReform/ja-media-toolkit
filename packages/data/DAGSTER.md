# Dagster operations

Dagster is the sole execution control plane for `packages/data`. This runbook
covers local operation and freezes the requirements for shared DEV. Ownership
and data contracts live in [ARCHITECTURE.md](ARCHITECTURE.md).

## Configuration model

Application configuration is a typed `pydantic-settings` model:

```text
process environment
  > adjacent .env.<environment>
  > config.<environment>.toml
  > model defaults
```

`JA_MEDIA_DATA_CONFIG` selects a non-default TOML file. Nested environment
overrides use Pydantic's standard double-underscore convention, for example
`JA_MEDIA_DUCKLAKE__POSTGRES_URL`. Stable endpoints, buckets, prefixes, schemas,
and UI URLs belong in TOML. Secrets and framework-required connection strings
belong in the adjacent dotenv file or the service manager.

Dagster itself still consumes `DAGSTER_HOME` and `DAGSTER_POSTGRES_URL`; Celery
consumes `JA_MEDIA_CELERY_BROKER_URL`. These are framework process boundaries,
not a second application configuration model.

## Disposable local stack

First-time setup:

```sh
cp packages/data/config.local.example.toml packages/data/config.local.toml
# Edit the bronze and services endpoints.
cp packages/data/.env.local.example packages/data/.env.local  # if needed
```

Docker Desktop must have host networking enabled so the code location inherits
host tailnet routing. Start the entire supported stack with one command:

```sh
docker compose -f deploy/data/local/compose.yaml up -d --wait
```

The first invocation builds the application image if it is absent. Python,
campaign, and template changes are bind-mounted. Restart only affected volatile
services; use `--build` only after dependency or image changes.

Validate application configuration and start the workbench:

```sh
cd packages/data
uv run ja-data doctor
uv run ja-data web
```

Open Dagster at <http://127.0.0.1:53000> and the operator workbench at
<http://127.0.0.1:8766/operator>.

Check externally configured dependencies from the code-location namespace:

```sh
docker compose -f deploy/data/local/compose.yaml exec -T dagster \
  ja-data doctor
```

Launch canonicalization from Dagster UI. The CLI equivalent exists for
debugging and automation, not as a second application workflow:

```sh
docker compose -f deploy/data/local/compose.yaml exec -T dagster \
  dagster job execute \
  --module-name ja_media_data.orchestration.dagster.definitions \
  --job canonicalization_campaign
```

Inspect services without deleting state:

```sh
docker compose -f deploy/data/local/compose.yaml ps
docker compose -f deploy/data/local/compose.yaml \
  logs --tail=100 dagster dagster-daemon dagster-server-worker
```

`down --volumes` is valid only for this disposable local project after
confirming the Docker context. It is never a DEV or production procedure.

## Shared DEV prerequisites

Assume the Debian VM already has Docker Engine, Compose v2, tailnet routing, and
ACLs. Before starting the DEV Compose file, provide:

1. `ja_media_data_dev` and `ja_media_dagster_dev` databases on the existing
   PostgreSQL server, preferably with separate roles.
2. A DEV Garage bucket or prefix writable by the control plane but not bronze,
   plus separate read-only bronze credentials.
3. A tailnet-only name for the Caddy gateway and a tailnet bind address for
   AMQP workers.
4. `/etc/ja-media/config.dev.toml` for stable configuration and a protected
   `/etc/ja-media/.env.dev` for DSNs, object credentials, and broker
   credentials.
5. Backup coverage for both PostgreSQL databases. Compose owns RabbitMQ's
   persistent volume, DEV vhost, and application principal.

No new PostgreSQL server, MinIO deployment, public ingress, or container
registry is required for the first DEV increment. The deployment home is
[`deploy/data/dev/`](../../deploy/data/dev/).

The DEV topology separates volatility:

- persistent: Caddy, RabbitMQ, Dagster webserver, and Dagster daemon;
- existing external: Garage, PostgreSQL, and first-party service gateway;
- replaceable: code location, server worker, and operator WebUI; and
- on demand: native Apple/CUDA workers.

The supported first-time sequence is:

```sh
docker compose --env-file /etc/ja-media/.env.dev \
  -f deploy/data/dev/compose.yaml config --quiet
docker compose --env-file /etc/ja-media/.env.dev \
  -f deploy/data/dev/compose.yaml up -d --build --wait
docker compose --env-file /etc/ja-media/.env.dev \
  -f deploy/data/dev/compose.yaml --profile tools run --rm preflight
```

Changing product Python or templates restarts only `code-location`,
`server-worker`, and `operator-web`. Only Caddy port 8080 and AMQP port 5672
are published; Dagster's and FastAPI's internal ports are not host APIs.

## Acceptance contract

Shared DEV must prove:

- Dagster persists a run across webserver and code-location restarts;
- the code location reads bronze and reaches the AniList gateway;
- canonicalization publishes the expected live slice to DEV DuckLake;
- the operator UI links the product head to the producing run;
- a failed rerun leaves the preceding product head visible; and
- a queued native step survives while no native worker is attached.

The E1-B job and object-store protocol were removed after freezing the
transport-neutral worker envelope and invocation contracts. Experimental jobs
must not be registered in production `Definitions`.
