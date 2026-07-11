# Consumer services and episode bundles

Status: proposed application boundary. Orchestrator internals are not a public
API.

## Goal

Give audio, subtitles, Audiobookshelf, and research tools one stable answer to
"what artifacts should this consumer use for this episode?" without requiring
them to understand the transformation graph.

## Bundle contract

An episode bundle selects exact immutable artifacts under a named policy:

```json
{
  "schema_version": 1,
  "kind": "episode-bundle",
  "bundle_id": "bundle-01J...",
  "locator": {"namespace": "anilist", "id": "15451", "episode": "3"},
  "policy": "display-v1",
  "binding_id": "binding-01J...",
  "audio": {
    "result_id": "result-audio-01J...",
    "media_type": "audio/mp4",
    "sha256": "..."
  },
  "subtitles": [
    {
      "result_id": "result-sub-01J...",
      "language": "jpn",
      "alignment_result_id": "result-align-01J...",
      "sha256": "..."
    }
  ],
  "checks": [
    {"name": "audio-is-japanese", "version": "lid-v2", "passed": true}
  ],
  "created_at": "..."
}
```

Bundle policies intentionally differ:

| Policy | Required selections/checks |
| --- | --- |
| `display-v1` | original/selected audio, readable selected subtitles |
| `audiobookshelf-v1` | episode binding, portable AAC, publication metadata |
| `voice-research-v1` | Japanese LID, aligned Japanese text, dialogue segmentation eligibility |

There is no universal "gold episode."

## Storage and current selection

Write immutable bundle versions:

```text
published/episodes/anilist/15451/e003/display-v1/versions/{bundle_id}.json
```

After validation, update a small current pointer last:

```text
published/episodes/anilist/15451/e003/display-v1/current.json
```

Pinned datasets/publications reference immutable `bundle_id`, never the moving
current pointer.

## Service relationship to orchestration

Services do not query Dagster/Prefect event tables. They index/read published
bundles and artifact references. This keeps:

- public media contracts stable across orchestrator replacement;
- consumer availability independent of worker/control-plane downtime;
- application queries fast and domain-oriented;
- orchestration metadata private and operational.

A shared media-catalog service is deferred until repeated indexing/query logic
proves it removes duplication. The initial options are:

1. audio/subtitle services incrementally index the bundle fields they need;
2. one narrow bundle resolver is added as a complete first-party service slice.

Do not make several services independently scan all silver artifact prefixes.
They consume the much smaller published-bundle prefix or a resolver API.

## Bronze audio API

Keep capture reads beneath the existing `/api/v1/audio` gateway:

```text
GET /captures
GET /captures/{capture_id}
GET /captures/{capture_id}/audio/content
```

Bronze does not enter the episode/profile route. Once a bundle selects a silver
audio artifact, the existing route may project it:

```text
GET /series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}
```

If legacy filesystem and new bundle providers conflict, require explicit
provider selection or return `409`; never pick by modification time.

## Subtitle API

Keep current Kitsunekko routes unchanged and add neutral tracks:

```text
GET /tracks?series_namespace=anilist&series_id=15451&provider=
GET /tracks/{provider}/{track_id}
GET /tracks/{provider}/{track_id}/content
```

No `/api/v2/subtitles` is needed because old routes retain their meaning.
Unmapped bronze tracks appear in series discovery but not episode-filtered
queries. Silver/bundle tracks expose measured language and alignment provenance
without overwriting declared-language hints.

## Required subtitle refactor

Before adding neutral routes, split the 717-line
`kitsunekko_subtitles/app.py` into app assembly, legacy routes, content response,
identity resolution, and neutral track modules. Split the 434-line core client
while preserving `HttpKitsunekkoSubtitlesClient` compatibility.

## Complete service slice

If a bundle resolver/catalog service becomes necessary, it must include:

- focused runtime modules and tests;
- `GET /healthz` with bundle-index freshness and conflict state;
- `GET /metrics` with scans, failures, bundles, conflicts, and staleness;
- a typed core protocol/client using gateway discovery;
- Compose, Caddy, healthcheck, and Prometheus discovery;
- docsite purpose, API, configuration, degraded behavior, and verification.

## Acceptance

- a service resolves/streams a bundle while orchestration is offline;
- old Kitsunekko and filesystem-derived audio routes remain compatible;
- current pointers never affect pinned historical datasets;
- bundle conflicts or missing referenced objects are visible/degraded;
- services do not need Dagster/Prefect packages or database access;
- application responses do not expose storage credentials or orchestration IDs.
