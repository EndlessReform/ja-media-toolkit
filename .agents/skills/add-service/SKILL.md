---
name: add-service
description: Add or substantially extend a first-party LAN service in ja-media-toolkit. Use when creating a service API, adding a service to the shared Docker deployment or Caddy gateway, adding its packages/core SDK client, defining health or Prometheus metrics, registering monitoring discovery, or documenting a new service end to end.
---

# Add a Service

Treat a service as a complete vertical slice. A backend is not finished when
its FastAPI routes work; it is finished when repo tools can consume it through
the core SDK, the deployment can run and monitor it, and the docsite explains
it.

## Start Here

1. Read `references/service-pattern.md` completely before editing.
2. Inspect the closest existing service instead of inventing a parallel shape.
3. Write down the service contract: ownership, source data, refresh behavior,
   gateway prefix, durable state, health semantics, and client operations.
4. Implement the smallest complete vertical slice.

Good reference services:

- `anime_crosswalk`: generated SQLite data, shell-managed refresh, metrics.
- `kitsunekko_subtitles`: generated data plus another service's read-only DB.
- `anilist_search`: process-managed refresh and a flexible SDK response model.

## Required Outcome

Unless the user explicitly narrows the scope, a new first-party service must
include all applicable parts:

- Service implementation under
  `envs/services/src/ja_media_services/<service_name>/`.
- Settings, startup entry point, focused modules, and tests under
  `envs/services/tests/`.
- `/healthz` and `/metrics` endpoints with meaningful operational state.
- Console scripts in `envs/services/pyproject.toml` where operators need them.
- A `packages/core/src/ja_media_core/<service_name>.py` SDK contract and HTTP
  client, exported from `ja_media_core/__init__.py`, with core tests.
- Shared gateway discovery through `[services].root_url`, plus a narrow
  service-specific environment override.
- A Compose service and Caddy route in the deployment that owns it.
- An entry in that deployment's Prometheus HTTP-SD targets.
- User/operator documentation in the Starlight docsite, including config,
  endpoint, SDK, health, metrics, and verification examples.

Do not leave deployment, SDK, observability, or docs as an implied follow-up
unless the user asks for a deliberately phased implementation.

## Guardrails

- Keep data and client contracts durable; treat models and runtimes as
  replaceable.
- Put first-party service runtime code in `envs/services`, not in
  `packages/core`. Core owns lightweight contracts and clients only.
- Route clients through the shared gateway. Never hard-code a deployed host.
- Add dependencies with `uv add` from `envs/services`; do not hand-edit
  dependency lists.
- Use `uv run` from the environment documented in `AGENTS.md`; never invoke
  `python` or `python3` directly.
- Preserve the repo's file-size limits. Stop and extract a coherent module
  before crossing 500 lines; actively reconsider boundaries after 300.
- Do not read `.env` directly. Service URLs belong in global config, not
  `.env`; secrets remain in `.env`.
- Prefer a useful, service-specific health response over a shallow liveness
  check. Prometheus metrics must reveal stale data and failed refreshes, not
  merely that the last validated database was good.

## Verification

Run focused tests first, then the complete affected environments:

```sh
uv run pytest packages/core/tests

cd envs/services
uv run pytest tests
```

Build the docsite after documentation or navigation changes:

```sh
cd site
npm run build
```

For deployment changes, render Compose config before attempting a live start:

```sh
docker compose config
```

Report any intentionally deferred checklist item in the final handoff.
