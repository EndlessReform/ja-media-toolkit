# Disposable local data stack

Copy `packages/data/config.local.example.toml` to
`packages/data/config.local.toml`, replace the two example tailnet endpoints,
and optionally copy `.env.local.example` to `.env.local` for Garage secrets.

```sh
docker compose -f deploy/data/local/compose.yaml up -d --wait
```

The first invocation builds the application image. Normal source changes are
bind-mounted; restart `dagster`, `dagster-daemon`, or
`dagster-server-worker` as appropriate without rebuilding. Rebuild only after
dependency or Dockerfile changes.

All PostgreSQL, MinIO, RabbitMQ, and Dagster ports bind to loopback. This stack
is disposable; `down --volumes` is valid only after confirming the local Docker
context.
