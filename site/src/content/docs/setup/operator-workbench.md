---
title: Lakehouse operator workbench
description: Start and use the Dagster-backed canonicalization workbench.
---

The loopback workbench explains current data products in domain terms. Dagster
owns the asset graph, run status, step status, and raw logs. DuckLake owns
canonical inputs, candidates, quarantine rows, and product lineage. The page
joins those facts; it does not run a second pipeline engine.

## Start the local control plane

From the repository root:

```sh
docker compose -f deploy/data/local/compose.yaml up -d --wait
```

Dagster is available at <http://127.0.0.1:53000>. It must be running before the
workbench starts. The checked-in local TOML gives the host and containers the
same disposable DuckLake destination.

## Start the workbench

```sh
cd packages/data
uv run ja-data doctor
uv run ja-data web
```

Open <http://127.0.0.1:8766/operator>. `pydantic-settings` loads stable values
from `config.local.toml` and secrets from `.env.local`; no shell exports are
required.

The deployment applies migrations through its one-shot schema service. The WebUI
creates persistent clients at startup but never applies DuckLake or PostgreSQL
DDL.

## Read the canonicalization desk

The conclusion product appears first. The initial three-row preview can expand
to a server-paged product table. Expanding one candidate row performs one
bounded locator query.

The pipeline spine is derived from the same registered Dagster job that the
operator lens presents:

```text
Resolver proposals → Automatic acceptance → Canonical inputs
```

Selecting a card loads that stage's bounded domain table. Resolver quarantine,
automatic acceptances, and canonical selections are stage-native views rather
than one global failure table.

Each card separates:

- the current product head and its producing run;
- the latest Dagster step execution; and
- whether the product is current for today's inputs, recipe, and override
  revision.

These may disagree after a partial failure. That is expected and is the reason
the fields are separate.

## Inspect runs and logs

The workbench Run Log is a curated Dagster view. `Run #N` is Dagster's
monotonically increasing run-record ID; the UUID under the information control
is the stable internal identity. The detail page lists steps and any compacted
per-item progress, then links to Dagster for raw events, logs, retries, and
cancellation.

If 93 item products commit and the 94th item fails, the run is failed while the
page still reports 93 advanced items. The run status does not roll back durable
domain products.

## Run the campaign

E2.1 is read-only, so launch canonicalization from Dagster:

```sh
docker compose -f deploy/data/local/compose.yaml exec -T dagster \
  dagster job execute \
  --module-name ja_media_data.orchestration.dagster.definitions \
  --job canonicalization_campaign
```

Refresh the workbench after completion. The old `ja-data run`, `targets`,
`plan`, and recipe-registry commands no longer exist; they belonged to the
removed custom planner/executor.

## Current safety boundary

The workbench remains read-only except for the existing explicit `ja-data bind`
CLI. The next gate will add transactional binding/canonical candidate decisions
and reexecution through the same application boundary. It will not add generic
workflow controls or duplicate Dagster's UI.
