# Metaflow Control Plane

This directory is a moveable deployment unit for a shared homelab Metaflow
control plane. It is intentionally not coupled to ja-media services, project
buckets, or application schemas.

The deployment assumes the node is on Tailscale. The VM's MagicDNS name should
be `<machineid>-metaflow`, and this stack owns that name:

- `http://<machineid>-metaflow/` serves the Metaflow UI.
- `http://<machineid>-metaflow:8080/` serves the Metaflow metadata API.

Metaflow uses two different storage paths:

- Postgres stores run metadata: flows, runs, steps, tasks, artifact records,
  and UI indexes.
- Garage/S3 stores Metaflow's own run artifacts and code packages. Flow code
  talks to Garage directly; the metadata service does not proxy large media,
  checkpoints, or datasets.

Keep this distinction strict. The Metaflow system datastore is for Metaflow's
runtime bookkeeping. Durable project data should live in project-owned buckets
or prefixes, and flows should pass stable object URIs or manifests through
Metaflow instead of making Metaflow's internal paths the application registry.

## Services

| Service | Default bind | Purpose |
| --- | --- | --- |
| `caddy` | ports 80/8080 | Tailnet entrypoint |
| `postgres` | internal only | Metaflow metadata database |
| `migration` | none | One-shot database migration runner |
| `metadata` | internal only | Metaflow metadata API |
| `ui-backend` | internal only | Metaflow UI/API backend |
| `ui` | internal only | Metaflow UI frontend built from the official source repo |

Only Caddy publishes host ports. The Metaflow containers stay on the Compose
network.

## Host Prerequisite

The host must be joined to your tailnet before starting this stack. Its
MagicDNS name should be `<machineid>-metaflow`:

```sh
tailscale status
tailscale status --json | jq -r '.Self.DNSName'
```

This stack binds Caddy to `0.0.0.0` by default. Treat the VM, Tailscale
membership, and host firewall rules as the security boundary. Do not publish
this VM's ports on an untrusted network interface.

## Sizing

This stack is the Metaflow control plane, not the worker pool. It stores
metadata, serves the UI/API, and writes Metaflow system artifacts to object
storage. Large datasets, media files, model checkpoints, and GPU work should be
handled by flow workers and project storage directly.

Recommended starting point:

| Use | CPU | RAM | Local disk |
| --- | --- | --- | --- |
| Single-user / homelab | 2 vCPU | 2-4 GiB | 20-50 GiB SSD-backed storage |
| Several active projects | 2-4 vCPU | 4-8 GiB | 50-100 GiB SSD-backed storage |

Prefer reliable SSD-backed storage for Postgres. Large artifact capacity belongs
in the object store, not on this VM/container host.

## Setup

```sh
cd deploy/metaflow
cp .env.example .env
$EDITOR .env
docker compose pull
docker compose up -d
```

Before starting the stack, fill in every value below in `.env`.

| Variable | Required value |
| --- | --- |
| `POSTGRES_PASSWORD` | New strong password for the Metaflow Postgres user. |
| `METAFLOW_DATASTORE_SYSROOT_S3` | Metaflow-owned Garage/S3 prefix, for example `s3://metaflow-system/metaflow`. |
| `METAFLOW_S3_ENDPOINT_URL` | Garage S3 API endpoint reachable from this host and from flow workers. |
| `AWS_ACCESS_KEY_ID` | Access key for the Metaflow system Garage principal. |
| `AWS_SECRET_ACCESS_KEY` | Secret key for the Metaflow system Garage principal. |

These values can usually stay at their defaults:

| Variable | Default |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | `metaflow` |
| `METAFLOW_SERVICE_BASE_IMAGE` | `netflixoss/metaflow_metadata_service:latest` |
| `METAFLOW_SERVICE_IMAGE` | `metaflow-metadata-service:local` |
| `METAFLOW_UI_IMAGE` | `metaflow-ui:local` |
| `METAFLOW_UI_REF` | `v1.3.13` |
| `POSTGRES_IMAGE` | `postgres:15-alpine` |
| `CADDY_IMAGE` | `caddy:2.8-alpine` |
| `CADDY_BIND_ADDR` | `0.0.0.0` |
| `CADDY_UI_PORT` | `80` |
| `CADDY_METADATA_PORT` | `8080` |
| `MF_UI_FRONTEND_PORT` | `3000` |
| `MF_METADATA_PORT` | `8080` |
| `MF_MIGRATION_PORT` | `8082` |
| `MF_UI_METADATA_PORT` | `8083` |
| `POSTGRES_DB` | `metaflow` |
| `POSTGRES_USER` | `metaflow` |
| `METAFLOW_DEFAULT_DATASTORE` | `s3` |
| `AWS_DEFAULT_REGION` | `garage` |
| `LOGLEVEL` | `INFO` |
| `PREFETCH_RUNS_SINCE` | `2592000` |
| `PREFETCH_RUNS_LIMIT` | `25` |

`POSTGRES_PASSWORD` is a plain Postgres password, not a URL or connection
string. This stack builds a tiny local derivative of the upstream Metaflow
service image because the upstream migration script does not escape generated
passwords safely when constructing its Postgres URL.

If the password contains `$`, quote or escape it according to Docker Compose
`.env` rules so Compose does not treat part of the password as another
environment-variable reference before the container ever sees it.

Check the metadata service through Caddy:

```sh
curl http://<machineid>-metaflow:8080/ping
```

Expected response:

```text
pong
```

Open the UI backend/UI, if the selected image includes the bundled frontend:

```text
http://<machineid>-metaflow/
```

## S3 Smoke Test

The UI being up proves Caddy, Postgres, and the Metaflow web services are
running. It does not prove that flow workers can write to the Metaflow S3
datastore. Test that with the included tiny flow.

Run this from a machine that has network access to both the Metaflow metadata
API and the Garage S3 endpoint:

```sh
cd deploy/metaflow

export METAFLOW_DEFAULT_METADATA=service
export METAFLOW_SERVICE_URL=http://<machineid>-metaflow:8080
export METAFLOW_DEFAULT_DATASTORE=s3
export METAFLOW_DATASTORE_SYSROOT_S3=s3://metaflow-system/metaflow
export METAFLOW_S3_ENDPOINT_URL=http://garage.example.lan:3900
export AWS_DEFAULT_REGION=garage
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

uv run --with metaflow --with boto3 --with requests ./smoke_s3_flow.py run
```

Expected result:

- the command exits successfully
- the terminal prints a `MetaflowS3SmokeFlow/<run-id>` run path
- the run appears in the Metaflow UI
- the `message`, `flow_name`, `run_id`, and `task_id` artifacts are visible

If this fails before a run appears, check `METAFLOW_SERVICE_URL`. If the run
starts but artifact persistence fails, check the Garage endpoint, bucket/prefix,
and Metaflow system principal credentials.

## Client Configuration

On any machine that runs flows against this control plane, configure the
Metaflow client with the metadata service and the same Garage-backed system
datastore:

```sh
export METAFLOW_DEFAULT_METADATA=service
export METAFLOW_SERVICE_URL=http://<machineid>-metaflow:8080
export METAFLOW_DEFAULT_DATASTORE=s3
export METAFLOW_DATASTORE_SYSROOT_S3=s3://metaflow-system/metaflow
export METAFLOW_S3_ENDPOINT_URL=http://garage.example.lan:3900
export AWS_DEFAULT_REGION=garage
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

Those credentials should be the Metaflow system principal. Project flows should
use separate project principals when they read or write project data buckets.

## Bucket Boundary

Recommended shape:

```text
s3://metaflow-system/metaflow/
  Metaflow-owned code packages, task artifacts, logs, and UI-readable runtime data.

s3://<project>-data/...
  Canonical project inputs and derived datasets.

s3://<project>-models/...
  Promoted checkpoints, exported models, and serving candidates.
```

Metaflow can produce project artifacts, but the final durable location should be
owned by the project, not by an opaque Metaflow run path.

## Caddy / Reverse Proxy

Caddy is part of this deploy unit. It exposes the UI on port `80` and the
metadata API on port `8080` for the VM's MagicDNS name. Do not mount it under a
learner- or application-facing API gateway path like `/api/v1/metaflow`; this
is shared ML control-plane infrastructure, not a product API.

The UI frontend is served from its own container. Requests under `/api/*` on
port `80` are stripped and forwarded to the UI backend; all other UI traffic
goes to the frontend container. The metadata API on port `8080` is the endpoint
used by Metaflow clients.

## Sources

- Metaflow service: <https://github.com/Netflix/metaflow-service>
- Metaflow infrastructure docs: <https://docs.metaflow.org/getting-started/infrastructure>
- Metaflow UI: <https://github.com/Netflix/metaflow-ui>
