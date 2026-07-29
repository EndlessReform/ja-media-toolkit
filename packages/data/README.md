# ja-media data products and operator workbench

This package compiles immutable Garage captures into versioned DuckLake
products. Dagster owns execution; FastAPI provides the domain-specific operator
view and transactional human decisions.

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing product, campaign,
orchestration, worker, or workbench boundaries.

## First local setup

```sh
cp packages/data/config.local.example.toml packages/data/config.local.toml
```

Edit the copied TOML to use the real bronze and first-party service tailnet
endpoints. Stable settings belong there. If Garage needs explicit credentials:

```sh
cp packages/data/.env.local.example packages/data/.env.local
```

Only secrets and framework connection strings belong in `.env.local`.
`pydantic-settings` loads process environment first, then `.env.local`, then
`config.local.toml`; nested overrides use names such as
`JA_MEDIA_BRONZE__BUCKET` and `JA_MEDIA_BRONZE__PREFIX`. Both bronze location
fields are required, and an empty prefix means the root of a dedicated bucket.
Unknown TOML keys fail startup.

The former package `.env` is no longer read by `ja-data`. Move its stable values
into TOML and only the required credentials into `.env.local`; delete it after
confirming `ja-data doctor` reports the intended environment.

## Daily local workflow

From the repository root:

```sh
docker compose -f deploy/data/local/compose.yaml up -d --wait
```

Then:

```sh
cd packages/data
uv run ja-data doctor
uv run ja-data web
```

Open:

- operator workbench: <http://127.0.0.1:8766/operator>
- Dagster execution and logs: <http://127.0.0.1:53000>

Use the operator workbench for domain rows and human decisions. Use Dagster for
launching jobs, retries, cancellation, execution status, and raw logs. `ja-data`
does not provide a second campaign launcher or product-execution CLI.

Normal source changes are bind-mounted into the application containers; restart
the affected application service without rebuilding. Rebuild only after a
dependency or Dockerfile change. See
[`deploy/data/local/README.md`](../../deploy/data/local/README.md).

For the native-worker-to-DEV development loop, copy
`.env.worker.dev.example` to the ignored `.env.worker.dev` and follow
[DAGSTER.md](DAGSTER.md#native-worker-dev-loop). The worker reuses bronze
credentials from `.env.local`; its DEV broker and staging secrets live only in
`.env.worker.dev`.

## Application commands

```sh
uv run ja-data web
uv run ja-data doctor
uv run ja-data bind anilist 10087 14 --capture <capture-id>
uv run ja-data bind anilist 10087 14 --unbind
```

`bind` is temporary until the operator workbench owns transactional mutations.
Schema installation is the Compose deployment's idempotent `data-schema-init`
one-shot, not a normal operator command.

## Shared DEV

Local PostgreSQL, MinIO, RabbitMQ, and Dagster state are disposable. Persistent
shared execution belongs to the Debian DEV deployment under
[`deploy/data/dev/`](../../deploy/data/dev/). That deployment reuses external
Garage and PostgreSQL, keeps RabbitMQ and Dagster durable, and replaces code
locations, workers, and the WebUI independently. Its
[`README.md`](../../deploy/data/dev/README.md) defines the required protected
environment variables, where each credential comes from, and the first-start
RabbitMQ bootstrap behavior.

## Tests

```sh
uv run --directory packages/data pytest tests
```

The integration suite expects the disposable local PostgreSQL service.
