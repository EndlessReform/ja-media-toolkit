# Phased implementation roadmap

Status: proposed sequencing. No implementation is authorized.

Each service change remains a complete vertical slice: runtime, core client,
health, metrics, Compose/Caddy, monitoring discovery, tests, and docsite.

## Phase 0: fixtures and hard-limit refactor

1. Save sanitized bronze v1 fixtures for zero, one, and multiple subtitles.
2. Add malformed, unsupported, and missing-object fixtures.
3. Record deterministic legacy capture/track IDs.
4. Split `kitsunekko_subtitles/app.py` below 500 lines without behavior change.
5. Split the core Kitsunekko client along contract/transport responsibilities.

Gate: golden API responses and existing clients remain unchanged; all touched
hand-written files respect limits.

## Phase 1: bronze contract and read path

1. Add `ja_media_core.bronze` models, parsing, validation, and protocols.
2. Add read-only S3 settings/adapter and persistent rebuildable index.
3. Add capture list/detail/Range-content routes and core client operations.
4. Add health/metrics, Compose/Caddy, monitoring, and docsite updates.
5. Add bootstrap plus cursor/ETag incremental reconciliation.

Gate: fixture-backed S3 tests list and Range-read captures; old filesystem audio
tests remain green; ordinary requests do not rescan S3.

## Phase 2: orchestration spike

Implement the workflow in `02-orchestration-options-and-spike.md` in Dagster and
Prefect without changing production services.

Use isolated experimental environments; add no framework dependency to
`packages/core`. Prefer disposable code under a bounded spike directory or
internal design branch until selection. Stop after one weekend even if the
comparison is imperfect; the output is a decision record, not two supported
orchestration deployments.

Gate: written comparison and explicit user sign-off selecting Dagster, Prefect,
or DVC/Snakemake fallback. Stop here if no candidate earns its complexity.

The weekend does not include production migration, a permanent control-plane
deployment, backfilling the full corpus, generalized GPU scheduling, automatic
garbage collection, or rewriting consumer services.

## Phase 3: domain identity and storage adapter

1. Add `EpisodeHint`, `EpisodeBinding`, `MediaArtifactRef`, and `EpisodeBundle`.
2. Implement capture-keyed source partitions/inputs.
3. Implement accepted binding and episode-keyed partition registration.
4. Implement the Garage reference/I/O adapter and generic artifact envelope.
5. Prove idempotent commit and recovery after interrupted uploads.

Gate: two captures bind to logical episodes, derived output stores in Garage,
and loss of orchestration state does not make the artifact unintelligible.

## Phase 4: first real silver vertical slice

1. Extract reusable `portable-aac-v1` transformation/verification from
   audio-library materialization.
2. Add audio LID with complete model/config versioning.
3. Add blocking Japanese-audio check.
4. Add `display-v1` bundle materialization and immutable/current storage.
5. Run targeted stale/rebuild scenario across one series.

Gate: passing episode produces a current bundle; dub remains blocked; changing
LID version identifies and rebuilds selected partitions without overwriting
history.

## Phase 5: consumer-service integration

1. Add bundle/result contracts and core client/repository operations.
2. Project selected silver audio through existing derived-audio semantics.
3. Add provider-neutral subtitle tracks while preserving legacy routes.
4. Choose bundle indexing in existing services versus one narrow resolver based
   on measured duplication—not architectural taste.
5. Complete service deployment/monitoring/docsite slices.

Gate: services resolve and stream while orchestration/workers are offline;
provider ambiguity is explicit; current pointers do not affect pinned history.

## Phase 6: capture-first ingest and publication

1. Capture and notify/register bronze before derivation.
2. Separate binding, derivation/check, bundle, and publication application
   services.
3. Add compatibility `audio-library ingest --capture-to-bronze` orchestration.
4. Add publication record and atomic Audiobookshelf projection.

Gate: forced failure after each commit resumes at the correct stage; deleting
and rebuilding the target from a pinned bundle reproduces media/metadata.

## Phase 7: subtitle silver assets

Add only when demanded by a caller:

```text
normalization -> LID -> alignment -> cleaning
```

Each definition uses the generic artifact envelope and stable cue/result
contracts. Do not create operation-specific service routes.

Gate: exact input versions and diagnostics are visible; raw provider tracks
remain unchanged; selected bundle tracks pass required policy checks.

## Phase 8: pinned datasets and shard export

1. Define the first real evaluation/research manifest.
2. Add deterministic tar packing and JSONL index.
3. Commit export manifest last and benchmark representative access.
4. Add lifecycle/pinning integration and conservative GC dry-run.

Gate: unpacked and packed readers yield identical sample IDs/hashes; benchmark
justifies storage; GC cannot select pinned inputs.

## Verification matrix

```text
uv run pytest packages/core/tests
cd packages/frontend && uv run pytest tests
cd envs/services && uv run pytest tests
cd site && npm run build
docker compose config
```

Add selected-orchestrator tests in its own declared environment. Never invoke
ad hoc `python`; use the appropriate `uv run` environment.

## Deployment boundary

Repository work stops at locally validated configuration. The user owns Proxmox
VM/LXC creation, remote deployment, restarts, compute-node setup, and remote
infrastructure inspection. Handoffs must provide exact config, health checks,
backup/restore, upgrade, rollback, and worker-online/offline behavior.

Live API smoke tests occur only when explicitly requested. No agent deploys or
SSHes into Proxmox, workstation, or remote compute.
