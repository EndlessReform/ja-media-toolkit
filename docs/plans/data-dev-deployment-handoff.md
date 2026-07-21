# Data DEV deployment handoff

Status: repository implementation complete; remote DEV provisioning and live
acceptance pending. Written 2026-07-19 for continuation in a new task.

The durable architecture is documented in
[`packages/data/ARCHITECTURE.md`](../../packages/data/ARCHITECTURE.md), and the
operator runbook is
[`packages/data/DAGSTER.md`](../../packages/data/DAGSTER.md). This document is
only the restart point for unfinished deployment work.

## Completed repository work

- Dagster is the sole execution authority. The custom planner, executor, run
  ledger, and execution CLI have been removed.
- A supported campaign is one actual Dagster asset job structurally bound to
  its operator presentation. `ja_media_data.campaigns.CAMPAIGNS` feeds both
  Dagster `Definitions` and the operator catalog. The code location refuses to
  load when a lens requires an asset absent from its resolved job.
- The duplicate `canonicalization-gate.toml` campaign definition is gone.
  Deployment TOML configures infrastructure, not executable program structure.
- The E1-B delayed-VAD job, spike storage protocol, scripts, tests, and object
  prefix conventions are gone. Versioned worker envelopes, transport-neutral
  environment invocation, and compacted handoff contracts remain.
- The persistent DEV stack lives in [`deploy/data/dev/`](../../deploy/data/dev/):
  Caddy, RabbitMQ, Dagster webserver/daemon, code location, server worker,
  schema one-shot, operator WebUI, and preflight.
- Caddy publishes HTTP on port 8080. Dagster lives under `/dagster`; the
  workbench lives under `/operator`. Their internal ports are not host APIs.
  AMQP 5672 is the only other published port.
- Compose owns RabbitMQ's volume, `ja-media-dev` vhost, and
  `ja_media_data_dev` principal. The operator does not provision these by hand.
- Stable application configuration uses `pydantic-settings` with priority:
  process environment, adjacent `.env.<environment>`, TOML, typed defaults.
- Immutable migrations are now grouped by authority under
  `packages/data/migrations/ducklake/` and
  `packages/data/migrations/control_postgres/`. Their filenames, contents, and
  checksums did not change.
- The obsolete migration plan that recommended retiring Dagster was deleted.
  Durable conclusions were retained in `packages/data/ARCHITECTURE.md`.

The working tree contains this complete increment but has not yet been
committed. Review `git status` before adding unrelated work.

## Validation already completed

- Full `packages/data` suite: 58 passed, 1 skipped.
- Focused campaign/configuration tests: 16 passed.
- Renamed migration roots against local PostgreSQL: 11 passed.
- Ruff: clean.
- Production Dagster definitions: loadable and valid.
- DEV Compose, including the `tools` profile: renders successfully.
- `ja-media/data-dev:dependencies`: builds successfully.
- Built image contains `ja-data-preflight` and loads production definitions.
- Temporary wheel contains both migration trees.
- Astro documentation site: builds successfully.

Warnings were limited to upstream Dagster beta/deprecation notices and existing
third-party `parse-torrent-title` escape warnings.

## User-owned inputs still required

Do not inspect or operate the remote host from an agent task. The user owns
these infrastructure operations and will place secrets directly on the VM.

1. Application PostgreSQL:
   - database `ja_media_data_dev`;
   - application role with ownership/migration rights;
   - DuckLake catalog schema `ja_media_ducklake_dev`;
   - control schema `ja_media_control`.
2. Dagster PostgreSQL:
   - clean database `ja_media_dagster_dev`;
   - separate Dagster-owned role;
   - old spike history may be discarded only by the user.
3. Garage:
   - read-only access to the existing bronze bucket/prefix;
   - writable DEV bucket or prefix, recommended
     `s3://ja-media-dev/ducklake/dev/`;
   - endpoint, region, addressing style, and credentials confirmed from the
     container network namespace.
4. Tailnet:
   - stable DNS name for the Caddy entry point;
   - VM tailnet address for `JA_MEDIA_DEV_HTTP_BIND` and
     `JA_MEDIA_DEV_AMQP_BIND`;
   - ACLs allowing operator HTTP and intended native workers to reach AMQP.
5. Host files:
   - `/etc/ja-media/config.dev.toml` from `config.dev.example.toml`;
   - `/etc/ja-media/.env.dev` from `.env.dev.example`, mode `0600`;
   - a URL-safe RabbitMQ password because Compose embeds it in the AMQP URL;
   - PostgreSQL and Garage credentials placed only in `.env.dev`.
6. Backups:
   - both PostgreSQL databases covered;
   - RabbitMQ definitions/volume coverage is useful but queued work is not
     permanent domain truth;
   - DEV silver objects are reproducible, while operator decisions are not.

## First persistent DEV bring-up

Follow [`deploy/data/dev/README.md`](../../deploy/data/dev/README.md). From the
repository root on the DEV VM:

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

Do not add PostgreSQL, MinIO, or a second RabbitMQ deployment to this Compose
file. PostgreSQL and Garage are external homelab infrastructure; RabbitMQ is
owned by this stack.

For normal source changes:

```sh
docker compose \
  --env-file /etc/ja-media/.env.dev \
  -f deploy/data/dev/compose.yaml \
  restart code-location server-worker operator-web
```

Do not rebuild or restart Caddy, RabbitMQ, Dagster webserver, or Dagster daemon
for ordinary Python, template, or CSS changes. Rebuild the dependency image
only after `uv.lock`, package metadata, or the Dockerfile changes.

## Live acceptance required before the next gate

The repository tests do not replace this deployment proof:

1. Preflight reports every dependency `ok` without displaying credentials.
2. Dagster UI loads through `<gateway>/dagster` and shows the code location.
3. Operator workbench loads through `<gateway>/operator`.
4. Run `canonicalization_campaign` against a real bounded/live corpus slice.
5. Confirm canonical rows and the committed product head appear in the
   workbench with a link to the producing Dagster run.
6. Restart Dagster webserver and code location; confirm run history persists.
7. Cause or select a failed rerun and confirm the preceding successful product
   head remains visible rather than being represented as rolled back.
8. Confirm the code location can read bronze, write only to the DEV DuckLake
   destination, and reach the AniList service gateway.
9. Confirm no container can write to bronze.

If preflight fails, fix the owning boundary rather than adding compatibility
aliases, shell exports, personal configuration mounts, or one-off container
commands.

## Commit boundary

After review and live acceptance, commit the current increment as the DEV
control-plane/configuration cutover. Do not mix E2.2 native-worker behavior into
that commit. The deployment itself is user-owned; an agent may prepare or
validate repository changes locally but must not connect to the VM or restart
remote services.

## Next gate: E2.2 supported native worker

E2.2 begins only after the persistent DEV acceptance above succeeds. Its goal
is not another orchestration experiment; it turns the surviving worker
contracts into one ergonomic native-worker path.

Required outcome:

```text
real canonical inputs
  → deterministic bounded selection
  → Dagster/Celery capability queue
  → native Apple worker from a checkout (no image rebuild)
  → Garage staging outputs and marker-last result
  → server-side object verification
  → idempotent DuckLake product commit
  → compacted handoff rows and bounded cleanup
```

E2.2 must prove:

- a multi-episode job remains queued while no compatible worker exists;
- Dagster/control-plane restart does not lose the job;
- a laptop or workstation can run one documented doctor/start command and
  drain the queue without receiving DuckLake, application-PostgreSQL, or
  Dagster-storage credentials;
- worker death and acknowledgement loss reuse the same fingerprinted output;
- media bytes never pass through Celery;
- the server verifies every declared output before advancing a product head;
- temporary request/result objects compact and clean according to the
  protected-run retention policy.

Do not reintroduce `e1b_delayed_vad`, phase-named queues, frozen singleton S3
selection keys, per-item permanent JSON manifests, a second executor, or a
generic `BaseStage` hierarchy. The unfinished gates remain itemized in
[`lakehouse-phase-e2-control-plane-and-workers.md`](lakehouse-phase-e2-control-plane-and-workers.md).
