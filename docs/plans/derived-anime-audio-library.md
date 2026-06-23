# Indexed Anime Audio: Current State and Next Work

## Durable context

The derived anime audio filesystem and manifest contracts are documented in
[`docs/audio-library/README.md`](../audio-library/README.md). Those artifacts
remain authoritative. Audiobookshelf is a consumer projection, and the SQLite
service index is disposable state that must be rebuildable from manifests.

The service is a read/index/serve boundary over files produced by the
interactive frontend workflow. It does not own ingestion or ffmpeg jobs.

## Implemented baseline

The manual CLI workflow can:

- identify a series through the AniList service;
- inspect source media and select episode/audio-stream mappings;
- materialize verified portable AAC artifacts through ffmpeg;
- atomically publish artifacts, the authoritative `.ja-media.json`, cover art,
  and Audiobookshelf metadata;
- resume from manifest checkpoints without rereading valid completed work.

The indexed anime-audio service can:

- rebuild SQLite from immediate-child `.ja-media.json` manifests at startup;
- validate manifests, containment, and referenced artifact existence;
- resolve series, episodes, and artifacts by AniList ID, episode key, and
  profile;
- serve artifact content with range support without exposing host paths;
- perform an explicit full reconciliation through `POST /reconcile`;
- report readiness, reconciliation failures, and indexed counts through
  `/healthz` and `/metrics`;
- run behind `/api/v1/audio` with a typed client in `packages/core`.

The shared schema encoder and decoder now live in `packages/core`. SQLite stays
on local container storage; only the artifact library is mounted from NFS.

Current service operations are:

```text
GET  /series/{anilist_id}
GET  /series/{anilist_id}/episodes
GET  /series/{anilist_id}/episodes/{episode_key}
GET  /series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}
GET  /series/{anilist_id}/episodes/{episode_key}/artifacts/{profile}/content
POST /reconcile
GET  /healthz
GET  /metrics
```

## Remaining work

The next work is deliberately consumer-driven:

1. Keep the service index current as manifests change.
2. Audit the durable manifest schema and service SDK before adding consumers.
3. Make subsync prefer already-derived audio and support an identity-only
   invocation.
4. Expose the complete indexed inventory through the service and core SDK.

Service-owned ingestion, persisted jobs, workers, and REST-triggered ffmpeg
are not part of this plan. They should be introduced only if a concrete
workflow needs the service to own asynchronous conversion.

## Phase 2a: keep the index current

### Consistency model

Use filesystem events for normal low-latency updates and reconciliation for
repair:

```text
atomic manifest publication
  ├─ filesystem event → debounced series refresh within seconds
  └─ missed event → periodic incremental scan repairs the index

startup or operator request → full reconciliation
```

Watch creation, replacement, movement, and deletion of immediate-child
`.ja-media.json` files. Debounce event bursts so one CLI publication results in
one refresh. A changed series should be reparsed and replace only its own index
rows transactionally; deleting a manifest should remove that series.

NFS may not deliver events caused by another client to the service host. This
does not threaten artifact correctness; it can only leave the disposable index
temporarily stale. Retain a configurable fallback scan every few minutes and
`POST /reconcile` as the repair paths.

The fallback scan must be metadata-only:

- enumerate immediate series directories and manifest paths;
- compare manifest identity, `mtime_ns`, and size with the last indexed values;
- parse and validate only new or changed manifests;
- remove rows for manifests proven absent after a complete scan;
- do not read, hash, or probe unchanged audio artifacts.

This causes directory and manifest metadata operations on the NFS-backed HDD,
but not recurring reads through media contents. Filesystem watching itself
does not scan the disk.

### Operational contract

Add settings for watcher enablement, debounce duration, and fallback interval.
Health and metrics should expose bounded watcher state, last successful
incremental scan, and refresh failures. Startup reconciliation and explicit
full reconciliation remain supported.

Tests must cover event coalescing, manifest replacement, deletion, missed-event
repair, invalid changed manifests, and service restart from a preexisting
index.

## Phase 2b: validate core contracts

Before subsync and inventory add more callers, review the two durable
interfaces already moved into `packages/core`:

1. The versioned manifest records and JSON mapping.
2. The typed anime-audio service client.

The manifest audit should verify that schema version 1:

- contains enough stable identity, source provenance, profile, and measured
  artifact data to rebuild the service index;
- preserves string episode keys even though the current CLI only materializes
  ordinary positive integers;
- distinguishes source observations from derived artifact observations;
- keeps host-specific absolute paths out of persisted data;
- defines optionality deliberately rather than as an accident of the current
  AniList response;
- rejects incompatible schema versions and malformed nested records clearly;
- round-trips without silently dropping provenance.

The SDK audit should verify that:

- `AnimeAudioClient` expresses consumer operations rather than HTTP details;
- series, episode, and artifact records expose stable identities and measured
  facts without filesystem paths;
- artifact metadata and content retrieval have consistent profile defaults;
- 404/missing-artifact behavior is distinguishable from service/configuration
  failure so subsync can make the correct fallback decision;
- content retrieval can support a cache without requiring callers to recreate
  service URLs;
- inventory records introduced in 2d reuse the same nested contracts where
  practical rather than defining a competing representation.

Add focused compatibility and malformed-input tests wherever this review finds
an implicit contract. Do not redesign schema version 1 merely for aesthetic
consistency: make a versioned change only when an existing representation
cannot safely express a required consumer behavior.

ffprobe, ffmpeg, verification, atomic publication, and audio-library
orchestration remain in `packages/frontend` for now. Avoid having subsync
import another feature's application module; extract a neutral frontend helper
only when 2c demonstrates concrete shared behavior. `packages/media` is not a
required part of this phase.

## Phase 2c: consume derived audio from subsync

### MKV input

When subsync receives an MKV, retain it as the promotion target and fallback
audio source. Once AniList ID and episode identity are known:

1. Query `AnimeAudioClient.artifact()` for the configured profile.
2. If present, fetch/cache the derived artifact and use it for playback and
   synchronization.
3. If the artifact is absent, or the service is unavailable, warn and
   materialize audio from the supplied MKV exactly as today.

The lookup must happen before reading the MKV for audio. A service miss must
not break the existing local-file workflow. Promotion still writes the chosen
subtitle beside the original MKV, never beside the downloaded cache file.

### No input file

Make the media positional argument optional when enough identity is supplied
to resolve derived audio, for example:

```text
ja-media subsync tui --anilist 101573 --episode 1
```

In this mode:

- resolve and fetch audio through `AnimeAudioClient`;
- perform remote subtitle lookup and all synchronization work normally;
- disable the promote action because there is no authoritative media path for
  a sidecar;
- explain the disabled action in status/help text rather than failing when the
  key is pressed;
- fail clearly at startup if identity is incomplete or no derived artifact is
  available.

Keep playback/cache identity separate from the optional promotion target in
the subsync application model. Do not overload `MaterializedAudio.source_path`
with both meanings.

Tests must cover cache hit before MKV access, 404 and unavailable-service
fallback, promotion beside the original MKV after a cache hit, identity-only
startup, and disabled promotion.

## Phase 2d: expose inventory

Add one complete, read-only inventory operation:

```text
GET /inventory
```

The response should contain bounded top-level counts plus every indexed series,
its episode keys, and available artifact profiles. It must contain stable
identity and display metadata but no filesystem paths. The initial library is
small enough that pagination is unnecessary; add it only when measured payload
size justifies it.

Add corresponding core contracts and SDK operation:

```python
@dataclass(frozen=True)
class AnimeAudioInventory:
    series_count: int
    episode_count: int
    artifact_count: int
    series: tuple[AnimeAudioInventorySeries, ...]


class AnimeAudioClient(Protocol):
    def inventory(self) -> AnimeAudioInventory: ...
```

Inventory must be a projection of the same SQLite snapshot used by point
lookups. Test deterministic ordering, empty inventory, multiple profiles,
response parsing, and absence of host paths.

## Verification and completion

For each slice, run the affected core, media, frontend, and service tests.
Build the docsite and render Compose configuration for service changes. Keep
hand-written files within the repository line limits.

This plan is complete when filesystem changes become visible promptly with
bounded NFS repair traffic, the manifest and SDK contracts are explicit and
consumer-ready, subsync avoids rereading MKVs whenever derived audio exists,
identity-only subsync works without promotion, and consumers can enumerate the
complete indexed inventory.
