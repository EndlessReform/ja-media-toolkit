# First-Party Service Pattern

Use this checklist for a service deployed as part of the repository-owned
`ja-media-services` stack. Third-party applications such as Audiobookshelf
belong in their own deployment directory and may not need a core SDK.

## 1. Define the contract

Before implementation, establish:

- The service's single responsibility and authoritative inputs.
- Which state is durable and where it lives.
- How data is initialized, refreshed, validated, and atomically published.
- The stable gateway prefix: `/api/v1/<service>`.
- The client operations that repo tools actually need.
- What makes the service healthy, degraded, stale, or unavailable.
- Which failures must be visible in metrics.

Prefer explicit source identity and update provenance. A currently readable DB
does not prove that its refresh pipeline is healthy.

## 2. Add the service runtime

Create:

```text
envs/services/src/ja_media_services/<service_name>/
├── __init__.py
├── app.py
├── settings.py
└── <focused domain modules>
```

Add `ingest.py` and `smoke.py` only when the service builds or validates a
durable artifact. Split database access, refresh orchestration, parsing, and
metrics by responsibility rather than growing `app.py`.

Use Pydantic Settings with a service-specific prefix. Standard settings are:

- `host`
- `port`
- `root_path`
- `log_level`
- data or DB paths
- refresh interval, when applicable

Register operator-facing commands under `[project.scripts]` in
`envs/services/pyproject.toml`. If a new dependency is required, run `uv add`
from `envs/services`; do not edit the dependency array manually.

## 3. Build operational endpoints

Every new service exposes:

- `GET /healthz`
- `GET /metrics`

`/healthz` should report enough structured JSON to distinguish healthy,
degraded, stale, and not-ready states. Include relevant facts such as row
counts, source revision, last successful update, last attempt, consecutive
failures, or artifact validation.

Use an unsuccessful HTTP status when the service cannot serve its contract.
A degraded but usable stale-data state may remain HTTP 200 if the JSON status
clearly says `degraded`; document that choice.

`/metrics` uses Prometheus exposition format and low-cardinality labels.
For refreshable data, include at least:

- last successful update timestamp;
- consecutive update failures;
- useful corpus or index size;
- current artifact validity or readiness.

Do not put exception messages, IDs, paths, titles, or other unbounded values in
labels. Request counters and latency histograms are optional until traffic
makes them useful.

Test both endpoints, including unavailable and degraded states.

## 4. Add the core SDK

Create:

```text
packages/core/src/ja_media_core/<service_name>.py
packages/core/tests/test_<service_name>.py
```

The module should normally contain:

- A `Protocol` describing the client operations used by repo workflows.
- Frozen dataclasses for durable request and response contracts.
- A small synchronous standard-library HTTP implementation unless an existing
  project convention requires async.
- `<SERVICE>_BASE_URL_ENV` for a direct machine-local override.
- `<SERVICE>_GATEWAY_PATH` for the stable gateway route.

Resolve the base URL with `ja_media_core.services.service_base_url`:

1. explicit constructor URL;
2. service-specific environment override;
3. `[services].root_url` plus the gateway path;
4. a clear configuration error.

Export the public contract from `ja_media_core/__init__.py`.

Core tests must cover gateway resolution, direct override precedence, URL
construction, response parsing, no-result behavior, and meaningful HTTP
errors. Keep wire-format flexibility at the edge and expose typed values to
callers where the contract is stable.

## 5. Integrate the deployment

Add the service to the Compose project that owns it. For the current shared
stack this is root `compose.yaml`.

Specify:

- image/build ownership;
- restart policy;
- service environment;
- durable or read-only volumes;
- dependencies that reflect real startup requirements;
- a container healthcheck;
- the service command.

Do not publish a backend port merely for convenience when Caddy is its intended
front door.

Add a `handle_path /api/v1/<service>*` route to `site/Caddyfile`. Remember that
`handle_path` strips the gateway prefix before proxying, so application routes
remain `/healthz`, `/metrics`, and domain paths.

If the repository later moves this stack under
`deploy/ja-media-services/`, keep its Compose, monitoring, and operator files
together there. Do not move the stack as incidental work for one service.

## 6. Register Prometheus discovery

Find the owning deployment's HTTP-SD target file:

```sh
rg --files | rg 'prometheus.*targets|targets.*json|observability'
```

Add the new metrics endpoint to that deployment's target group. Use the
gateway's stable network name and set `__metrics_path__` to
`/api/v1/<service>/metrics` when multiple services share one host.

Keep labels stable and useful for dashboards:

- deployment or stack;
- service;
- environment, if the deployment distinguishes environments.

Validate the JSON and verify that Prometheus's discovered target resolves to
the same endpoint an operator can curl. If no discovery artifact exists yet,
create it beside the owning deployment rather than under the Python package.

## 7. Update documentation

User and operator documentation belongs in `site/src/content/docs/`.

Update all applicable surfaces:

- `services/<service>.md`: purpose, API, examples, SDK usage, health, metrics.
- `guides/services.md`: add the service to the overview.
- `setup/services.md`: deployment and verification steps.
- `setup/config.md`: SDK discovery and service-specific override.
- `site/astro.config.mjs`: navigation only when autogeneration does not expose
  the page appropriately.

Starlight renders the frontmatter title. Start body headings at H2 and do not
repeat an H1 page title.

Commands should be human-readable and copy-pasteable. Use a `ROOT_URL`
placeholder or shell variable when the deployment URL varies; do not bake in a
real host from one machine. Document successful response shapes and degraded
behavior rather than showing only a happy-path curl.

## 8. Test the complete slice

At minimum:

1. Run focused service tests from `envs/services`.
2. Run core SDK tests from the repository root.
3. Build the Starlight site.
4. Run `docker compose config`.
5. Check changed hand-written files against the 300/500-line limits.
6. When a live stack is available, curl the gateway API, `/healthz`, and
   `/metrics`, then inspect the Prometheus target.

Do not claim the service is complete if its SDK, monitoring registration, or
operator documentation is missing.
