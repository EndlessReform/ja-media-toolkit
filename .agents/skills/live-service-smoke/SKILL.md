---
name: live-service-smoke
description: Use when the user asks to test, smoke-test, verify, check, or debug first-party ja-media services against live/prod/LAN/tailnet, including wording like "test against live on LAN", "see if remote has it", or "check prod". Use config autodiscovery and SDK clients before asking for service URLs.
---

# Live Service Smoke Tests

## Core Rule

When the user asks to test first-party ja-media services against "live",
"prod", "remote", "LAN", or "tailnet", do not ask for a base URL first.
Assume the user has already configured
`~/.config/ja-media-toolkit/config.toml` or `JA_MEDIA_CONFIG`.

This is allowed when limited to application-level API/client smoke tests.
It is not permission to SSH, inspect hosts, operate containers, deploy,
restart services, or hard-code private URLs into repo files.

## Config Discovery

The durable discovery contract lives in `packages/core/src/ja_media_core/config.py`.

Priority:

1. Explicit config path, if the tool supports one.
2. `JA_MEDIA_CONFIG`.
3. `~/.config/ja-media-toolkit/config.toml`.

The shared gateway root is `[services].root_url`. First-party SDK clients add
their gateway route, for example Anime Audio adds `/api/v1/audio`.

Service-specific environment overrides such as `ANIME_AUDIO_BASE_URL` are also
valid for the current shell, but do not write them into repo files.

## Preferred Pattern: Use The SDK

Use the relevant `packages/core` SDK client whenever one exists. Keep the probe
small and read-only unless the user explicitly asks otherwise.

Example Anime Audio smoke test:

```sh
PYTHONPATH=packages/core/src uv run python - <<'PY'
from ja_media_core.anime_audio import HttpAnimeAudioClient

client = HttpAnimeAudioClient()
series = client.series(183385)
episodes = client.episodes(183385)

print(series.title)
print(f"episodes={len(episodes)}")
for episode in episodes[:3]:
    artifact_names = [artifact.filename for artifact in episode.artifacts]
    print(
        episode.episode_key,
        artifact_names,
        f"subtitles={len(episode.subtitles)}",
    )
PY
```

For subtitle checks:

```sh
PYTHONPATH=packages/core/src uv run python - <<'PY'
from ja_media_core.anime_audio import HttpAnimeAudioClient

client = HttpAnimeAudioClient()
subs = client.subtitles(183385, "1")
print([(sub.subtitle_id, sub.language, sub.title) for sub in subs])

if subs:
    content = client.subtitle_content(183385, "1", subs[0].subtitle_id)
    print(content[:120].decode("utf-8", errors="replace"))
PY
```

## Curl Pattern

Only derive a curl base URL from config at runtime. Do not paste or commit the
resolved tailnet URL into source, docs, tests, plans, or final answers.

```sh
base="$(
  PYTHONPATH=packages/core/src uv run python - <<'PY'
import os

from ja_media_core.anime_audio import (
    ANIME_AUDIO_BASE_URL_ENV,
    ANIME_AUDIO_GATEWAY_PATH,
)
from ja_media_core.services import service_base_url

url = service_base_url(
    None,
    (os.environ.get(ANIME_AUDIO_BASE_URL_ENV),),
    ANIME_AUDIO_GATEWAY_PATH,
)
if not url:
    raise SystemExit("missing [services].root_url or ANIME_AUDIO_BASE_URL")
print(url.rstrip("/"))
PY
)"

curl -fsS "$base/series/183385" | jq .
curl -fsS "$base/series/183385/episodes" | jq 'length'
curl -fsS "$base/series/183385/episodes/1/subtitles" | jq .
```

If a service has a direct override, prefer its SDK constructor or service
module constant instead of reconstructing paths by hand.

## Reporting

Report only the useful result: status code, counts, matched IDs, response
shape, and any error payload. Avoid printing private hostnames unless the user
explicitly asks for endpoint debugging.

If config discovery fails, say that no configured service root was found and
point to `site/src/content/docs/setup/config.md`. Only then ask the user for
the missing configuration detail.

If a live request fails with network or DNS errors, mention that the tailnet or
LAN route may not be reachable from the current machine. Do not try remote
infrastructure operations.
