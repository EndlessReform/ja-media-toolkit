# LAN Kitsunekko Subtitle Service

A LAN-only subtitle inventory and retrieval service over the local Kitsunekko GitHub mirror.

## Non-Goals
- Public internet exposure.
- User authentication in v0.
- Writable corrections in v0.
- Postgres in v0.
- Full-text subtitle corpus search in v0.
- Quality scoring or forced-alignment ranking in v0.
- Object-storage backend in v0.
- No attempt to perfectly normalize all release titles in v0.

## Remaining Roadmap

### Phase I: Polish & Stability
- [ ] Add pagination / response caps for file-list endpoints.
- [ ] Harden filename episode parsing with real mirror examples.
- [ ] Add API tests for missing IDs, broad TVDB ambiguity, and kind-specific TVDB behavior.

### Phase II: Operations and Bulk Use
- [ ] Expand Prometheus metrics beyond DB gauges (request count, bytes served, build age).
- [ ] Loki-friendly structured logs.
- [ ] Grafana dashboard design.
- [ ] Bulk export endpoints (e.g., `.tar.gz` for a series).
- [ ] Better parser audit reports.
- [ ] Optional `subtitle-index.jsonl.gz` export.

### Phase III: Storage and Ranking
- [ ] Evaluate S3/Garage backend if bulk export becomes a bottleneck.
- [ ] Evaluate maintained filename normalization libraries.

## Open Questions
- Should subtitle content be served by `subtitle_id` only, or should stable encoded paths also be accepted?
- Should `.ass` and `.srt` duplicates be exposed separately, ranked, or grouped?
- What is the first client workflow: manual curl, Jellyfin/Sonarr helper, corpus mining, or ASR benchmark generation?
- Should TVDB broad lookup include both movie and TV matches, matching the crosswalk service, or should the Kitsunekko API default to TV-only?
- Should stale Git lock files be cleaned up automatically after interrupted fetches, or should the service fail loudly and wait for operator cleanup?
