---
title: Monitoring
description: Connect the ja-media service deployment to a central Prometheus server.
---

The service gateway publishes a Prometheus HTTP service-discovery document at:

```text
http://JA_MEDIA_HOST/prometheus-targets.json
```

The document advertises every metrics endpoint owned by the
`ja-media-services` deployment. Prometheus needs one permanent reference to
this URL; adding another metrics-enabled service to the deployment then only
requires updating the discovery document in this repository.

`JA_MEDIA_HOST` is the hostname or address that the Prometheus server uses to
reach this deployment. It may be LAN DNS, a Tailscale name, an IP address, or
`localhost:8080` during a local test.

## Configure Prometheus

Add this scrape job to the central Prometheus configuration:

```yaml
scrape_configs:
  - job_name: ja-media-services
    http_sd_configs:
      - url: http://JA_MEDIA_HOST/prometheus-targets.json
        refresh_interval: 5m
```

Replace `JA_MEDIA_HOST` with an address reachable from the Prometheus container
or host. This is the only deployment-specific value: Caddy renders each target
from the discovery request's `Host` header, including a non-default port when
present.

The discovery document supplies a separate `__metrics_path__` label for each
service behind the shared gateway, so the Prometheus job does not need repeated
scrape blocks.

Reload Prometheus using the mechanism provided by the homelab deployment. For
Prometheus installations with lifecycle reloads enabled, the request is:

```sh
curl -fsS -X POST http://PROMETHEUS_HOST:9090/-/reload
```

Replace `PROMETHEUS_HOST` with the address of the central Prometheus server.

## Verify discovery

First, confirm that the gateway serves valid discovery JSON:

```sh
ROOT_URL=http://JA_MEDIA_HOST
curl -fsS "$ROOT_URL/prometheus-targets.json" | jq .
```

Then verify every advertised metrics path directly:

```sh
curl -fsS "$ROOT_URL/api/v1/crosswalk/metrics" | head
curl -fsS "$ROOT_URL/api/v1/subtitles/metrics" | head
```

In Prometheus, open **Status → Service Discovery** and find
`ja-media-services`. Then open **Status → Targets** and confirm that both
targets are `UP`.

## What is currently monitored

The discovery document currently includes:

- Anime Crosswalk at `/api/v1/crosswalk/metrics`
- Kitsunekko Subtitles at `/api/v1/subtitles/metrics`

AniList Search is intentionally omitted because it does not yet expose a
Prometheus metrics endpoint. Its operational state remains available at
`/api/v1/anilist/health`.

## Repository ownership

The source of truth lives beside the deployment:

```text
deploy/ja-media-services/observability/prometheus-targets.json.tmpl
```

The docsite image copies that template to its static root. Caddy renders the
request host into valid JSON and serves it as `/prometheus-targets.json`. No
hostname, IP address, or Tailscale alias is committed to the repository. Do not
edit a copy on the Prometheus host.

## Import the Grafana dashboard

The repository includes an importable dashboard at:

```text
deploy/ja-media-services/grafana/ja-media-services.json
```

For the first few revisions, update Grafana manually:

1. Open **Dashboards → New → Import** in Grafana.
2. Upload the JSON file, or paste its complete contents.
3. Select the Prometheus data source that contains the
   `ja-media-services` scrape job.
4. Select the existing **ja-media services** dashboard when Grafana asks
   whether to overwrite it, then save.

The dashboard has a stable UID (`ja-media-services`), so re-importing a later
revision updates the same dashboard instead of creating another copy. Keep the
JSON file in this repository as the source of truth; changes made only in the
Grafana UI will be lost on the next import.

The initial dashboard shows:

- current and historical service availability from Prometheus's `up` metric;
- current Crosswalk and Kitsunekko rebuild-validation state;
- corpus and lookup sizes, which make unexpected refresh regressions visible;
- Prometheus scrape duration and sample counts.

The refresh gauges currently prove that the artifact being served passed
validation. They do not expose when the last refresh ran or how many refreshes
have failed. AniList Search is also absent because it does not yet expose a
Prometheus metrics endpoint. Those are deliberate limits of the current
application metrics, not dashboard omissions.
