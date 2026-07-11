# Dagster control-plane spike

This directory is a moveable, deliberately small Dagster OSS deployment for
the bronze-media orchestration spike. It runs a webserver, daemon, and gRPC
code location. PostgreSQL is external and is the sole store for Dagster run,
event, schedule, sensor, and asset metadata; no SQLite backend or PostgreSQL
container is included.

The bind-mounted `data/` directory contains compute logs and temporary local
artifacts. It is not the durable media store. Bronze and silver bytes belong in
Garage, and asset values should carry stable media references rather than large
payloads or Dagster run IDs.

## Prerequisites

- Docker with Compose v2
- An existing PostgreSQL database reachable from the Docker host
- A database/user dedicated to Dagster, with permission to create and migrate
  Dagster's tables
- PostgreSQL configured with a UTC database timezone

When PostgreSQL runs directly on the same macOS machine as Docker, set
`DAGSTER_PG_HOST=host.docker.internal`. A PostgreSQL `listen_addresses` value,
host firewall, and `pg_hba.conf` rule must permit the Docker connection. For a
LAN database, use its DNS name.

## Configure

```sh
cd deploy/dagster
cp .env.example .env
$EDITOR .env
```

Required settings:

| Variable | Meaning |
| --- | --- |
| `DAGSTER_PG_HOST` | PostgreSQL hostname as seen from the containers |
| `DAGSTER_PG_PORT` | PostgreSQL port; defaults to `5432` |
| `DAGSTER_PG_DB` | Existing database dedicated to Dagster |
| `DAGSTER_PG_USERNAME` | PostgreSQL role used by every Dagster process |
| `DAGSTER_PG_PASSWORD` | Password for that role; keep it only in `.env` |

Operational settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DAGSTER_VERSION` | `1.13.12` | Core and webserver version in the image |
| `DAGSTER_LIBRARY_VERSION` | `0.29.12` | Paired integration-library version |
| `DAGSTER_IMAGE` | `ja-media/dagster:1.13.12` | Built image tag |
| `DAGSTER_WEB_BIND_ADDR` | `0.0.0.0` | Host interface publishing the UI |
| `DAGSTER_WEB_PORT` | `3000` | Host port publishing the UI |
| `DAGSTER_DATA_PATH` | `./data` | Host path for logs/local artifacts |
| `DAGSTER_MAX_CONCURRENT_RUNS` | `2` | Queued-run concurrency limit |

The webserver has no built-in authentication. The default bind makes it
reachable through the homelab VM's addresses; use the tailnet and host firewall
as the access boundary. Set `DAGSTER_WEB_BIND_ADDR=127.0.0.1` when it should be
host-local only. Do not publish it directly to the public internet.

## Start and verify

Render the resolved configuration first; this also catches missing required
variables without contacting PostgreSQL:

```sh
docker compose --env-file .env config --quiet
docker compose --env-file .env build --pull
docker compose --env-file .env up -d
docker compose --env-file .env ps
```

Dagster initializes or migrates its schema when the services connect. Follow
startup and inspect failures with:

```sh
docker compose --env-file .env logs --tail=100 webserver daemon code-location
curl --fail http://127.0.0.1:3000/server_info
```

From another homelab or tailnet machine, replace `127.0.0.1` with the VM's DNS
name, for example `http://<machineid>-metaflow:3000`. The empty
`bronze_media_spike` code location is intentional; replace `definitions.py` as
the spike workflow takes shape.

## Lifecycle

```sh
docker compose --env-file .env stop
docker compose --env-file .env start
docker compose --env-file .env down
```

`down` removes containers and the Compose network. It does not remove the
external PostgreSQL database or the bind-mounted `data/` directory.

Before an upgrade, back up the Dagster database using the PostgreSQL owner's
normal process. Change `DAGSTER_VERSION`, `DAGSTER_LIBRARY_VERSION`, and
`DAGSTER_IMAGE` together, rebuild, then start the stack and review all service
logs. Rollback means restoring the database backup and restoring the prior
image/version set; do not run an older Dagster release against a schema already
migrated by a newer release.

## Current execution boundary

`DefaultRunLauncher` launches work beside the code-location process. This is
appropriate only for the initial control-plane and asset-model spike. Testing
an intermittently available remote worker or external job launcher remains an
explicit acceptance item in the bronze-media plan; PostgreSQL persistence does
not solve that compute-placement question.

## References

- [Dagster instance configuration](https://docs.dagster.io/deployment/oss/oss-instance-configuration)
- [Deploying Dagster with Docker Compose](https://docs.dagster.io/deployment/oss/deployment-options/docker)
