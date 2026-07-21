# Persistent shared DEV data stack

This is the deployment home for the Debian VM. PostgreSQL and Garage are
external homelab services. This stack owns RabbitMQ, Dagster, the data code
location, server worker, operator WebUI, and their Caddy gateway.

See `packages/data/DAGSTER.md` for ownership and acceptance contracts. Never
reuse or reset the disposable local volumes here.

## First-time host setup

Create the application and Dagster databases first, then install configuration:

```sh
sudo install -d -m 0750 /etc/ja-media
sudo install -m 0640 deploy/data/dev/config.dev.example.toml \
  /etc/ja-media/config.dev.toml
sudo install -m 0600 deploy/data/dev/.env.dev.example \
  /etc/ja-media/.env.dev
```

Replace every example endpoint and credential. The RabbitMQ password must be
URL-safe because Compose uses it in the Celery broker URL. It is a new secret
for this Compose-owned broker, not a credential obtained from another service.
For example, generate one before the first `up` with:

```sh
openssl rand -hex 32
```

On an empty `rabbitmq-data` volume, the RabbitMQ image creates the
`ja_media_data_dev` user and `ja-media-dev` vhost with this password. The
volume is authoritative after that first initialization: changing only
`RABBITMQ_PASSWORD` in `.env.dev` does not rotate the broker credential and
will prevent workers and Dagster from authenticating. Treat rotation as an
explicit broker administration operation and update `.env.dev` to the same
value.

Set `JA_MEDIA_DEV_HTTP_BIND` and `JA_MEDIA_DEV_AMQP_BIND` to the VM's tailnet
address; leaving them at `127.0.0.1` intentionally permits only host-local
access.

## Environment variable crosswalk

The protected `/etc/ja-media/.env.dev` file contains framework connection
strings and credentials. Stable endpoints, buckets, prefixes, schemas, and
service URLs belong in `config.dev.toml` instead.

| Variable | Used for | Source |
| --- | --- | --- |
| `RABBITMQ_PASSWORD` | Bootstraps the Compose-owned RabbitMQ user and is embedded in the Celery AMQP URL. | Generate a new URL-safe secret for this deployment before its first start. |
| `DAGSTER_POSTGRES_URL` | Dagster run, event, schedule, and daemon storage. | Connection URL for the externally provisioned `ja_media_dagster_dev` database and its dedicated role. |
| `JA_MEDIA_DUCKLAKE__POSTGRES_URL` | DuckLake catalog and application control schemas. | Connection URL for the externally provisioned `ja_media_data_dev` database and application role. |
| `JA_MEDIA_BRONZE__ACCESS_KEY_ID` | Read access to immutable bronze objects in Garage. | Existing Garage key with read-only access to the configured bronze bucket/prefix. |
| `JA_MEDIA_BRONZE__SECRET_ACCESS_KEY` | Secret paired with the bronze access key. | Existing Garage credential; do not generate locally unless provisioning that Garage principal. |
| `JA_MEDIA_DUCKLAKE__S3_ACCESS_KEY_ID` | Read/write access to DEV DuckLake objects in Garage. | Existing Garage key with access to the configured DEV bucket/prefix. |
| `JA_MEDIA_DUCKLAKE__S3_SECRET_ACCESS_KEY` | Secret paired with the DuckLake S3 access key. | Existing Garage credential; do not generate locally unless provisioning that Garage principal. |
| `JA_MEDIA_DEV_HTTP_BIND` | Host address publishing Caddy port 8080. | VM tailnet address for remote access, or omit/use `127.0.0.1` for host-only access. |
| `JA_MEDIA_DEV_AMQP_BIND` | Host address publishing RabbitMQ port 5672 for native workers. | VM tailnet address for remote workers, or omit/use `127.0.0.1` for host-only access. |

## Start and verify

```sh
docker compose \
  --env-file /etc/ja-media/.env.dev \
  -f deploy/data/dev/compose.yaml \
  config --quiet

docker compose \
  --env-file /etc/ja-media/.env.dev \
  -f deploy/data/dev/compose.yaml \
  up -d --build --wait

docker compose \
  --env-file /etc/ja-media/.env.dev \
  -f deploy/data/dev/compose.yaml \
  --profile tools run --rm preflight
```

Caddy exposes the workbench at `/operator` and Dagster at `/dagster` on port
8080. RabbitMQ's AMQP port 5672 is the only other published port. Its management
UI remains internal.

## Normal source-edit loop

Python, templates, static files, and Dagster configuration are bind-mounted.
After updating the checkout:

```sh
docker compose \
  --env-file /etc/ja-media/.env.dev \
  -f deploy/data/dev/compose.yaml \
  restart code-location server-worker operator-web
```

Rebuild `ja-media/data-dev:dependencies` only after `uv.lock`, package metadata,
or the Dockerfile changes. RabbitMQ, Dagster webserver, and Dagster daemon do
not restart for ordinary product code changes.
