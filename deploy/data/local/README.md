# Disposable local data stack

Copy `packages/data/config.local.example.toml` to
`packages/data/config.local.toml`, replace the two example tailnet endpoints,
and optionally copy `.env.local.example` to `.env.local` for Garage secrets.

```sh
docker compose -f deploy/data/local/compose.yaml up -d --wait
```

The first invocation builds the application image. The complete `packages/`
workspace is bind-mounted, so Python source and migration changes do not require
an image rebuild.

After pulling a change to the Compose mount definition itself, recreate the
application containers once so Docker adopts the new mount; `restart` alone
retains the old container specification:

```sh
docker compose -f deploy/data/local/compose.yaml up -d --no-build \
  --force-recreate --wait dagster dagster-daemon dagster-server-worker
```

For ordinary source changes, restart the long-running application containers:

```sh
docker compose -f deploy/data/local/compose.yaml restart \
  dagster dagster-daemon dagster-server-worker
```

For a new data migration, run the existing one-shot initializer before
restarting the applications:

```sh
docker compose -f deploy/data/local/compose.yaml run --rm data-schema-init
docker compose -f deploy/data/local/compose.yaml restart \
  dagster dagster-daemon dagster-server-worker
```

A full `down` followed by `up -d --wait` also runs the initializer and preserves
named volumes unless `--volumes` is supplied. Rebuild only after dependency,
lockfile, or Dockerfile changes.

All PostgreSQL, MinIO, RabbitMQ, and Dagster ports bind to loopback. This stack
is disposable; `down --volumes` is valid only after confirming the local Docker
context.
