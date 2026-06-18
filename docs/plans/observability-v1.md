# Observability v1

Goal: know immediately when a service's data is stale or an update pipeline has failed, and whether the host VM is about to run out of resources.

## Current state

Three backend services (plus one static site) behind Caddy:

| Service | `/metrics` endpoint | Update mechanism | Failure visibility |
|---|---|---|---|
| **anime-crosswalk** | ✅ Yes | Shell loop in `docker-entrypoint.sh` (git pull → ingest → smoke → atomic swap) | Logs to stderr only; DB metadata always reflects last *successful* build |
| **kitsunekko-subtitles** | ✅ Yes | Same shell-loop pattern as crosswalk | Same — failures invisible outside `docker logs` |
| **anilist-search** | ❌ No | Python daemon thread inside the FastAPI process | Rich in-memory `RefreshStatus` exposed via `/health` JSON only |
| **docs** (Caddy + Astro) | N/A | Static build at image time | Docker healthcheck sufficient |

### What exists today

Both `anime-crosswalk` and `kitsunekko-subtitles` render a `/metrics` route using `prometheus_client`, but the gauges are derived from the **validated DB metadata table**. This means:

- `anime_crosswalk_last_rebuild_success` and `kitsunekko_subtitles_last_rebuild_success` are **always 1.0** — they only get written after a successful ingest + smoke test. A failed update leaves the old (valid) DB serving, so the metric never goes to zero.
- There is no "last update timestamp" gauge — only row counts and the perpetually-true success flag.
- The shell-based update loops log failures to stderr but have no HTTP-visible failure state.

For `anilist-search`, the Python daemon thread tracks `consecutive_failures`, `last_failure`, `last_success_unix`, etc. in memory. This is excellent for the `/health` JSON endpoint but invisible to Prometheus since there is no `/metrics` route.

## What we need (prioritized)

### Tier 1: "The BIG last update time"

This is the single most important signal. If a service stops updating, its data rots silently.

**Gauges needed per service:**

- `service_last_successful_update_timestamp{service="anime-crosswalk|kitsunekko-subtitles|anilist-search"}` — Unix timestamp of the last successful data refresh
- `service_seconds_since_last_update{service=...}` — derived in Grafana from the above, or exposed directly

**Alert rule:** `service_seconds_since_last_update > expected_interval * 1.5` → page you

Expected intervals (from compose.yaml env vars):

| Service | Interval | Alert threshold |
|---|---|---|
| anime-crosswalk | 43200s (12h) | ~18h |
| kitsunekko-subtitles | 3600s (1h) | ~90m |
| anilist-search | 3600s (1h) | ~90m |

### Tier 2: Update failure visibility

Catch transient vs. persistent failures without flapping.

**Gauges needed:**

- `service_consecutive_update_failures{service=...}` — 0 means healthy, >0 means something broke
- `service_last_update_error{service=..., error="git_fetch|ingest|smoke_test|kaggle_download"}` — exception type or phase label

**Alert rule:** `service_consecutive_update_failures > 3` → page you

Threshold of 3 avoids paging on transient network blips (e.g., Kaggle API hiccup) while catching real outages.

### Tier 3: Request-level metrics (nice to have, not urgent)

These services are LAN-only and low-traffic. Basic HTTP counting is useful if anything starts relying on them programmatically, but not critical for v1.

- `http_requests_total{method, status, service}`
- `http_request_duration_seconds{service}` — histogram or summary

The existing Docker healthchecks cover "is it alive" for now.

### Tier 4: Node-level (VM OOM / disk pressure)

**node_exporter** is the standard answer. Run it as a sidecar container or host-level service and scrape port 9100.

Key metrics:

- `node_memory_MemAvailable_bytes` — OOM risk
- `node_filesystem_avail_bytes{mountpoint="..."}` — Docker volume space (especially important for Kitsunekko, which is a git mirror of subtitle files)
- `node_load1` / `node_cpu_seconds_total` — general health

**Alert rules:** available memory < 500MB, docker partition < 10GB free.

## Implementation plan

### Step 1: Add `/metrics` to anilist-search (~15 min)

The `RefreshStatus` dataclass already has everything needed (`last_success_unix`, `consecutive_failures`, `last_failure`). Add a `/metrics` route that renders `prometheus_client` gauges from the in-memory state, following the existing pattern in `anime_crosswalk/metrics.py` and `kitsunekko_subtitles/metrics.py`.

New gauges:

```
anilist_search_index_rows_total
anilist_search_last_refresh_success (1.0 if consecutive_failures == 0)
anilist_search_consecutive_refresh_failures
anilist_search_last_successful_update_timestamp
```

### Step 2: Expose shell-loop status via status file (~1–2 hours)

The blocker for anime-crosswalk and kitsunekko-subtitles is that their update loops live in **shell** (`docker-entrypoint.sh`), not Python. The `/metrics` handlers can only read the validated DB, which by definition never reflects a failure.

**Approach: status file (recommended for v1)**

Have each shell loop write a small JSON status file on every iteration:

```json
{
  "last_attempt_unix": 1718640000,
  "last_success_unix": 1718639000,
  "consecutive_failures": 0,
  "last_error": null
}
```

Paths:

- `/var/lib/anime-crosswalk/.update_status.json`
- `/var/lib/kitsunekko-subtitles/.update_status.json`

The existing Python `/metrics` handlers read this file alongside DB metadata and emit:

```
anime_crosswalk_last_successful_update_timestamp
anime_crosswalk_consecutive_update_failures
kitsunekko_subtitles_last_successful_update_timestamp
kitsunekko_subtitles_consecutive_update_failures
```

This requires minimal changes to `docker-entrypoint.sh` (a few `jq`/`printf` lines) and small additions to the two `metrics.py` renderers. No restructuring of the entrypoint or update logic is needed.

**Alternative: move update loop into Python**

Like anilist-search already does — a daemon thread inside the FastAPI lifespan handles polling, git fetch, ingest, and status tracking. This unifies all three services' patterns but requires restructuring `docker-entrypoint.sh` to just exec the Python process. Better long-term, but more invasive. Defer to v2 if needed.

### Step 3: node_exporter sidecar (~5 min)

Add to `compose.yaml`:

```yaml
node-exporter:
  image: prom/node-exporter:latest
  container_name: ja-media-node-exporter
  restart: unless-stopped
  pid: host
  volumes:
    - /proc:/host/proc:ro
    - /sys:/host/sys:ro
    - /:/rootfs:ro
  command:
    - '--path.procfs=/host/proc'
    - '--path.rootfs=/rootfs'
    - '--path.sysfs=/host/sys'
    - '--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)'
  ports:
    - "9100:9100"
```

Expose on the LAN or tailnet as appropriate. No need to route through Caddy — Prometheus scrapes it directly.

### Step 4: Prometheus scrape config (~5 min)

Add targets to your existing Prometheus config. The Caddyfile uses `handle_path` which strips prefixes before proxying, so `/metrics` arrives at each backend correctly through the gateway:

```yaml
scrape_configs:
  - job_name: 'ja-media-services'
    static_configs:
      - targets:
          - '<vm-ip>:8080'
    metrics_path: '/api/v1/crosswalk/metrics'
    honor_labels: true

  - job_name: 'ja-media-subtitles'
    static_configs:
      - targets:
          - '<vm-ip>:8080'
    metrics_path: '/api/v1/subtitles/metrics'

  - job_name: 'ja-media-anilist'
    static_configs:
      - targets:
          - '<vm-ip>:8080'
    metrics_path: '/api/v1/anilist/metrics'

  - job_name: 'ja-media-node'
    static_configs:
      - targets:
          - '<vm-ip>:9100'
```

### Step 5: Grafana panels + alert rules (~30 min)

Minimal dashboard layout:

- **One "last update" gauge per service** — seconds since last successful refresh, with a red threshold at 1.5× expected interval
- **One "consecutive failures" gauge per service** — 0 = green, >0 = yellow/red
- **One node panel** — available memory and disk space for the Docker partition

Alert rules in Prometheus (exported to Grafana or Alertmanager as desired):

```yaml
# Data staleness
- alert: ServiceDataStale
  expr: time() - service_last_successful_update_timestamp > on(service) expected_interval_seconds * 1.5
  for: 5m
  labels:
    severity: critical

# Persistent update failures
- alert: ServiceUpdateFailing
  expr: service_consecutive_update_failures > 3
  for: 10m
  labels:
    severity: warning

# Host resource pressure
- alert: LowMemory
  expr: node_memory_MemAvailable_bytes < 500 * 1024 * 1024
  for: 5m
  labels:
    severity: critical

- alert: LowDiskSpace
  expr: node_filesystem_avail_bytes{mountpoint=~"/var/lib/docker.*"} < 10 * 1024 * 1024 * 1024
  for: 10m
  labels:
    severity: warning
```

## Open questions

- **Alerting destination:** Prometheus can fire to Alertmanager, which can push to email, Slack, Tailscale Funnel, or anything else. Decide where the page lands before wiring rules.
- **Caddy metrics:** Caddy itself has a `prometheus` plugin that exposes upstream health and request counts. Useful if we want end-to-end visibility (e.g., "Caddy got the request but backend returned 502"). Low priority for v1.
- **Schema version tracking:** Both crosswalk and kitsunekko already store `schema_version` in DB metadata. Worth exposing as a gauge so we notice if a deploy rolls back to an old DB format.
