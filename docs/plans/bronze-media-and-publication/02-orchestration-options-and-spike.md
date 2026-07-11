# Orchestration options and spike

Status: required evaluation before silver implementation. No candidate is yet
selected.

## Goal

Determine whether an existing system can own the generic transformation
registry, lineage, checks, materialization history, stale detection, retries,
and backfills while leaving Japanese-media semantics and Garage storage under
our control.

## Field of candidates

| Candidate | Strength | Main mismatch | Burden |
| --- | --- | --- | --- |
| Dagster OSS | Assets, partitions, checks, versions, graph UI, and backfills | Intermittent heterogeneous workers need launcher/submission design | Light–medium |
| Prefect OSS | Simple Python flows, retries, UI, and pull workers on arbitrary machines | Run-centric; weaker durable asset/version model | Light–medium |
| DVC + Snakemake | Reproducible file DAGs, caching, and no service | Weak live application/catalog integration | Very low |
| Metaflow | Excellent ML-research flows, artifacts, resume, and local mode | Run-centric; shared/on-prem scaling becomes infrastructure work | Low locally, higher shared |
| Airflow | Mature scheduled operations | Many components; task/schedule-centric rather than asset-centric | Medium–high |
| Pachyderm | Versioned data, pipelines, provenance, and incremental processing | Kubernetes/storage-platform commitment | High |

Airflow and Pachyderm are not finalists. DVC/Snakemake is the fallback if an
always-on control plane does not earn its keep. Metaflow remains useful for
individual large research flows even if it does not own the maintained corpus.

Useful references:

- [Dagster asset versioning](https://docs.dagster.io/guides/build/assets/asset-versioning-and-caching)
- [Dagster partitions and backfills](https://docs.dagster.io/guides/build/partitions-and-backfills/backfilling-data)
- [Dagster run launchers](https://docs.dagster.io/deployment/execution/run-launchers)
- [Dagster instance configuration](https://docs.dagster.io/deployment/oss/oss-instance-configuration)
- [Prefect self-hosted server](https://docs.prefect.io/v3/concepts/server)
- [Prefect workers](https://docs.prefect.io/v3/concepts/workers)
- [DVC workflow](https://dvc.org/doc/command-reference/)
- [Metaflow architecture](https://docs.metaflow.org/internals/technical-overview)

## Timebox

Spend at most one weekend on this phase. Use the same fixtures and thin domain
functions in both candidates; do not build two polished implementations.

The spike may use ugly local configuration, one worker, and manual triggers.
It must not add production service routes, migrate existing data, deploy remote
infrastructure, or solve every executor. The purpose is to expose the hard
parts with working code and choose a direction.

If one candidate is clearly adequate halfway through and the other has already
failed a must-have criterion, stop early. If neither passes by the end, record
why and select DVC/Snakemake rather than extending the evaluation indefinitely.

## Required spike workflow

Use a tiny sanitized corpus with at least:

- two bronze captures that resolve to episodes;
- one audio track that passes Japanese LID;
- one dub that fails the Japanese policy;
- one subtitle needing normalization/alignment;
- one portable AAC output;
- one published `display-v1` episode bundle.

Model:

```text
bronze_capture[capture ID]
  -> episode_hint[capture ID]
  -> episode_binding[episode locator]
  -> episode_audio[episode locator]
  -> audio_language[episode locator]
  -> portable_aac[episode locator]
  -> display_bundle[episode locator]
```

Add a blocking Japanese-audio check. Change the LID model/recipe version and
show which partitions become affected. Rebuild only the affected series.

## Storage acceptance

The candidate must support an adapter that:

- stores large bytes directly in Garage;
- passes `MediaArtifactRef` values through orchestration rather than audio
  blobs;
- selects deterministic keys from asset/flow identity, partition, and data
  version;
- writes an immutable generic artifact envelope;
- verifies content before recording a successful materialization;
- can observe an externally committed bronze manifest;
- leaves artifacts intelligible if orchestration state is lost.

Dagster calls this an I/O manager. Prefect would use shared task/result/storage
helpers. The contract belongs in core and must not expose either framework's
internal run IDs as artifact identity.

## Compute acceptance

Run the control plane on a Proxmox-hosted VM or the existing Metaflow VM in an
isolated environment/container. Execute at least one transformation elsewhere:

- an intermittently available workstation;
- a separate LAN/tailnet guest;
- or a lightweight job submitted to an external compute API.

Record what happens while the target machine is offline:

- Does the job remain queued?
- Does it fail immediately?
- Can it be retried without duplicate output?
- Must a code server or agent remain running on the compute machine?
- Can one select Mac, CUDA, CPU, and ephemeral queues/resources clearly?

This is where Prefect may beat Dagster even if Dagster has the better asset
model.

## Human discoverability acceptance

At 3 a.m., the UI must make it possible to answer without SQL:

- What transformation families exist?
- What is upstream/downstream of LID or subtitle alignment?
- Which episode partitions are missing, failed, blocked, or stale?
- Which check rejected one episode?
- Which run/log produced an artifact?
- Can the affected series be selected and rebuilt?

Use human partition keys such as `anilist-15451/e003` and attach titles,
provider, model/recipe, content URLs, counts, and quality summaries as metadata.
Do not create one asset definition per episode.

## Operational acceptance

For Dagster, evaluate both:

1. `dg dev` with persistent `DAGSTER_HOME` and SQLite for the spike;
2. the likely always-on shape: webserver, daemon, code location, persistent
   SQLite, and process or external-job execution.

Postgres, per-run containers, Kubernetes, automatic schedules, and public
internet exposure are not part of the initial acceptance bar.

For Prefect, evaluate one server with SQLite plus a process worker on another
machine. Redis/Postgres and multi-server mode are out of scope.

## Decision record

The spike ends with a short comparison containing:

- code/config added for the same workflow;
- number of persistent processes and volumes;
- offline-worker behavior;
- graph/partition/check UI screenshots or observations;
- storage adapter complexity;
- rebuild behavior after an input/model change;
- recovery behavior after deleting orchestration metadata;
- limitations requiring custom domain code.

Only then amend this plan to select a framework. If both fail, use
DVC/Snakemake and implement only the small application catalog that remains.
