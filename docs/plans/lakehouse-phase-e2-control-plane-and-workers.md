# Phase E2: finish the Dagster cutover

Status: E2.0 and E2.1 complete. This file contains only unfinished gates. The
approved ownership model, vocabulary, module layout, retention rules, failure
semantics, and measured spike conclusions now live in
[`packages/data/ARCHITECTURE.md`](../../packages/data/ARCHITECTURE.md). The
operational contract lives in
[`packages/data/DAGSTER.md`](../../packages/data/DAGSTER.md).

The persistent DEV restart point, remaining infrastructure inputs, live
acceptance sequence, and E2.2 handoff are recorded in
[`data-dev-deployment-handoff.md`](data-dev-deployment-handoff.md).

## Constraints that carry through every gate

- Dagster is the sole execution control plane. FastAPI does not plan a second
  DAG or persist a second run/step ledger.
- Domain products and human decisions remain independently intelligible in
  DuckLake/PostgreSQL if Dagster history is lost.
- Heavy workers consume a versioned item envelope, read/write Garage, publish a
  marker last, and never receive catalog or decision-database credentials.
- A server step verifies worker output and publishes the domain product.
- Celery carries dispatch descriptors, never media or permanent business data.
- Human-decision blockers fail preflight and link to FastAPI; they do not park
  an indefinitely waiting worker step.
- Backlog selection is deterministic and bounded. `--limit` freezes one
  run-scoped selection; it is not a permanent partition scheme.
- Temporary handoffs compact into DuckLake and are garbage-collected according
  to the protected-run policy documented in the architecture.

## E2.2 — supported native worker boundary

Build on the frozen transport-neutral contracts. The E1-B Dagster job, storage
helper, smoke scripts, and object-prefix convention have been removed from the
production surface.

1. Implement `ja-data worker doctor/start --profile <name>` with checked-in
   profile configuration for capability queues and environment commands.
2. Promote the envelope and result marker into versioned supported contracts.
3. Freeze a bounded selection from real canonical inputs and dispatch VAD or
   demucs work to a native Apple worker without building an image.
4. Verify marker/object coverage server-side, publish a real product, and
   compact handoff rows into DuckLake.
5. Implement protected-run cleanup and prove retry/reuse after worker death and
   acknowledgement loss.

Gate: a multi-episode live slice can wait with no worker, survive control-plane
restart, drain through a native checkout, publish idempotently, and clean its
temporary handoff state. The worker has no DuckLake/PostgreSQL credential.

## E2.3 — first ASR product

1. Define the incremental transcript product at episode + recipe/model/bias
   fingerprint grain.
2. Reuse the E2.2 worker contract around the existing environment-owned
   `AsrBackend`; do not create stage inheritance or move model dependencies into
   `packages/data`.
3. Select a real multi-episode canonical/VAD slice and dispatch it through an
   Apple or CUDA capability queue.
4. Validate transcript schema, timing, coverage, checksums, and provenance
   before publishing silver rows.
5. Exercise no-worker waiting, worker restart, output reuse, and downstream
   failure after successful ASR.

Gate: real audio crosses server → queue → native ASR → Garage → server verify →
DuckLake without media in Celery or an application image rebuild.

## E2.4 — operator mutation and launch

1. Add evidence-bound binding override commands with optimistic concurrency.
2. Preview the exact affected domain rows and downstream asset closure before
   committing a decision.
3. Commit the transactional decision separately from execution.
4. Launch/reexecute the affected Dagster job through the gateway only after an
   explicit operator action; attach human context as Dagster tags.
5. Show which decision revision and product materialization a run consumed.

Gate: concurrent decisions cannot silently overwrite each other, the decision
audit survives Dagster loss, execution failure cannot roll back the decision,
and the workbench links directly to authoritative Dagster run detail.

## E2.5 — deployment acceptance and cutover proof

The shared DEV Compose surface now separates Caddy/RabbitMQ/Dagster from the
replaceable code location, server worker, and operator application. Remaining
work is live acceptance rather than another deployment design pass.

1. Run canonicalization and native-worker acceptance exclusively through the
   public campaign application service.
2. Compare product rows/fingerprints with the prior compiler outputs.
3. Inventory and delete the remaining proof-only executor, storage, scripts,
   variables, and fixtures.
4. Record the exact production prerequisites and rollback boundary.

Gate: a code/template change restarts only replaceable DEV services; no
supported route or CLI needs proof-era execution code; Dagster loss does not
make domain products uninterpretable; explicit sign-off authorizes production.
