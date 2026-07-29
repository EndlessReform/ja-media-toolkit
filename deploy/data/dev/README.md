# Persistent shared DEV data stack

This self-contained directory is the deployment bundle for the Debian VM. The
host does not clone the monorepo. PostgreSQL and Garage remain external; this
stack owns RabbitMQ, Dagster, the data code location, server worker, operator
WebUI, Caddy, and their named volumes.

See `packages/data/DAGSTER.md` in the development repository for ownership and
acceptance contracts. Never reuse or reset the disposable local-test volumes.

## Publish from the development checkout

The publisher requires a clean Git worktree and Docker Buildx. The registry is
operator environment, without an `https://` prefix, and Docker supplies stored
credentials from a prior login.

```sh
export JA_MEDIA_REGISTRY=registry.example.internal
docker login "$JA_MEDIA_REGISTRY"
scripts/publish-data-image
```

The default target is `linux/amd64`; set `JA_MEDIA_DATA_PLATFORM=linux/arm64`
only for an ARM64 deployment host. Successful output ends with:

```text
image=<registry>/ja-media/data-dev:git-<commit>
digest=sha256:<digest>
deploy=<registry>/ja-media/data-dev@sha256:<digest>
```

The digest output is useful for auditing, but normal deployment does not
require copying it. The host pulls the moving `:dev` tag and records the
registry-returned digest before changing Compose state.

## Install the bundle once

Copy the contents of `deploy/data/dev/` to a stable host directory such as
`/opt/ja-media-data-dev`. A tarball or ordinary file transfer is sufficient;
there is no repository history or source tree to install:

```sh
tar -C deploy/data/dev -czf /tmp/ja-media-data-dev.tgz .
```

On the host, install protected configuration from the bundled examples:

```sh
cd /opt/ja-media-data-dev
operator_group=$(id -gn)
sudo install -d -o root -g "$operator_group" -m 0750 /etc/ja-media
sudo install -o root -g "$operator_group" -m 0640 \
  config.dev.example.toml /etc/ja-media/config.dev.toml
sudo install -o root -g "$operator_group" -m 0640 \
  .env.dev.example /etc/ja-media/.env.dev
```

Root owns the directory and files, while only the deploying user's primary
group can traverse the directory and read configuration. This is required
because the unprivileged `control` process passes `.env.dev` to Docker Compose;
the secrets are not made world-readable.

Replace every example endpoint and credential. Generate the initial
Compose-owned RabbitMQ password with `openssl rand -hex 32`; it must be URL-safe
because it is embedded in the Celery broker URL. Set the HTTP and AMQP bind
variables to the VM's tailnet address, or leave them on `127.0.0.1` for
host-only access.

Set `JA_MEDIA_REGISTRY` in `/etc/ja-media/.env.dev` to the same registry
authority used by the publisher (without `https://` or a repository path).
Authenticate Docker once, then perform the first full reconcile:

```sh
./control login
./control reconcile
```

With no existing selection, `reconcile` pulls
`$JA_MEDIA_REGISTRY/ja-media/data-dev:dev`, resolves it to the digest actually
received, and records that immutable reference in the bundle's ignored
`.image.env`. It then pulls all images, creates or updates the complete Compose
topology, runs the idempotent schema initializer, waits for health, and runs
preflight. Use it again after changing Compose, Caddy, workspace, or Dagster
configuration; once an image is selected, a plain `reconcile` preserves it.

## Normal image update and rollback

An ordinary application update pulls and migrates the new image, atomically
records the selection in the bundle's ignored `.image.env`, replaces only the
code location, server worker, and operator WebUI, then runs preflight:

```sh
./control update
```

If the Compose-owned control plane is not already running, `update`
automatically performs a full reconcile instead. Initial installation therefore
does not depend on remembering a special command.

RabbitMQ, Dagster webserver, and Dagster daemon are not recreated. After the
replacement operator is healthy, Caddy is recreated so its upstream connection
cannot remain pinned to the removed operator container. If replacement or
preflight fails, the helper reports the preceding image. The new selection
remains visible for diagnosis. The preceding digest is retained automatically,
so the usual rollback is:

```sh
./control rollback
```

`update` defaults to `:dev`; explicit `git-<commit>` tags and `sha256:<digest>`
values are escape hatches for selecting a known build. `rollback` also accepts
either form. Full references are accepted only for the configured repository.
There is no need to find or copy a digest from Zot's UI.

Compose waits for the replacement operator's `/healthz` endpoint, then
recreates Caddy and waits for its proxied `/operator` health check before
preflight. Gateway checks identify Dagster and the operator separately so a
real routing failure names the affected upstream.

Prefer a quiet control plane for updates. The server worker receives a Celery
warm shutdown with a five-minute grace period, but this is a small DEV
deployment rather than a rolling worker pool. Use `reconcile` instead of
`update` after dependency/runtime changes so Dagster webserver and daemon move
to the same image generation.

Operational checks are deliberately small:

```sh
./control status
./control preflight
```

The host never builds an image. Docker credentials remain in Docker's
credential store. The registry address and selected digests are deployment
state, not source configuration: the address lives beside the other protected
host settings, while current and previous selections live in ignored files
next to `control`.

## Persistent state

- PostgreSQL owns Dagster history plus DuckLake/application control state.
- Garage owns bronze inputs and DEV DuckLake objects.
- `rabbitmq-data` owns the broker user, vhost, and queued messages. Changing
  only `RABBITMQ_PASSWORD` after initialization does not rotate that user.
- `fasttext-cache` retains the downloaded language-identification model across
  application replacements.
- `caddy-data` and `caddy-config` retain Caddy runtime state.

Caddy exposes `/operator` and `/dagster` on port 8080. AMQP 5672 is the only
other published port; RabbitMQ management and application ports remain
internal.

## Protected environment crosswalk

`/etc/ja-media/.env.dev` contains credentials and framework connection strings.
It is root-owned and mode `0640`, readable only by the deployment operator's
group. Stable endpoints, buckets, prefixes, schemas, and service URLs belong in
`config.dev.toml`. `bronze.bucket` and `bronze.prefix` must be repointed
together when changing bronze generations; an empty prefix selects the root of
a dedicated bucket.

| Variable | Purpose |
| --- | --- |
| `JA_MEDIA_REGISTRY` | Registry authority used to pull `ja-media/data-dev`; no scheme or path. |
| `RABBITMQ_PASSWORD` | Initial password for the Compose-owned broker principal. |
| `DAGSTER_POSTGRES_URL` | Dedicated external Dagster database and role. |
| `JA_MEDIA_DUCKLAKE__POSTGRES_URL` | External DuckLake catalog and control database. |
| `JA_MEDIA_BRONZE__ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` | Existing read-only bronze credential. |
| `JA_MEDIA_DUCKLAKE__S3_ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` | Existing DEV DuckLake read/write credential. |
| `JA_MEDIA_DEV_HTTP_BIND` | Tailnet or loopback address for Caddy port 8080. |
| `JA_MEDIA_DEV_AMQP_BIND` | Required DEV host tailnet address for native-worker AMQP. |
