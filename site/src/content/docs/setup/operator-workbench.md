---
title: Lakehouse operator workbench
description: Start and use the Dagster-backed canonicalization workbench.
---

The localhost workbench explains current data products in domain terms. Dagster
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

## Review rejected episode bindings

Open `/operator/resolution-review`. Local configuration is always preview-only.
With `environment = "dev"` or `"prod"`, accepting the paused proposal writes
the whole displayed crosswalk as one PostgreSQL control transaction. It does
not call the model again. The approval fails if the resolution materialization,
DuckLake snapshot, or binding revision changed while the review was open.

Binding decisions become inputs to the next canonical-input materialization;
Bronze is never renamed or mutated. Decisions to omit an extra are stored as
capture dispositions instead of imaginary episode locators. The Decision
History section shows each exact capture decision and its control revisions.
Reverse restores the recorded prior heads without a model call and refuses to
overwrite a newer operator decision.

The series rail opens on **Pending**. **Resolved** progressively discloses
source series with active, unreversed decision batches; selecting one shows the
applied capture crosswalk and a reversal form. Accept and reverse refresh both
rail counts immediately. A partial resolution can appear in both views: its
remaining issues stay Pending while its already-applied batches remain
correctable under Resolved.

The selected model endpoint receives the initial filenames/issues, prompt,
tool calls and outputs, AniList metadata, and bounded subtitle excerpts.
Model and base URL are unset by default and may be entered in the WebUI; those
two non-secret values persist in browser-local storage. Both are required for a
run; HTTP 422 names either missing field. First-party OpenAI uses the explicit
`https://api.openai.com/v1` base URL.
The WebUI's Max turns field overrides `[agent].max_turns` for one run and is
bounded to 1–30. Subtitle tools return `next_start_line` or `next_page`; use
that value to advance instead of repeating the first excerpt.
Responses use `store=false`. OpenAI Agents SDK tracing is disabled unless
`[agent].openai_tracing_enabled = true` (or the equivalent nested environment
override); even when enabled, this application excludes model and tool content
from spans. SDK debug logs redact model and tool content by default. A WebUI key
is request-only and is not stored in browser storage.
See the DEV deployment README for the complete variable list and promotion
procedure.
