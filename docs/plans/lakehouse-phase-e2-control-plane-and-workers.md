# Phase E2: finish the Dagster cutover

Status: E2.0, E2.1, the lightweight core dependency cut, and image-layer
optimization are complete. This file contains only unfinished gates. The
approved ownership model, vocabulary, module layout, retention rules, and
failure semantics live in
[`packages/data/ARCHITECTURE.md`](../../packages/data/ARCHITECTURE.md). The
operational contract lives in
[`packages/data/DAGSTER.md`](../../packages/data/DAGSTER.md).

The historical persistent DEV restart point and remaining infrastructure inputs
are recorded in
[`data-dev-deployment-handoff.md`](data-dev-deployment-handoff.md). Its
checkout-based commands are context, not the target deployment mechanism.

## Prerequisite — publish and accept checkout-free DEV

The data image now contains its application source and migrations, excludes the
optional audio stack, retains no `uv` binary/cache, and separates the large
third-party environment from source-changing layers. Remaining work is the OCI
publish to real Zot and live acceptance.

### One-command publish

The publisher is implemented as the 70-line
[`scripts/publish-data-image`](../../scripts/publish-data-image) shell wrapper
over Buildx; grounded usage lives in the
[DEV deployment README](../../deploy/data/dev/README.md). It requires a clean
worktree, delegates authentication to Docker, publishes immutable `git-<sha>`
and moving `dev` tags in one build, verifies both remote digests, and returns a
digest-pinned deployment reference. It never contacts the DEV host.

Local Zot 2.1.18 integration proved same-commit retry, a committed source-only
update, retention and execution of the old rollback digest, dirty-worktree
refusal, and unavailable-registry failure without a deployment reference. The
source-only update shared 9 of 13 filesystem layers and added 3,471,830
compressed layer bytes. Do not add Bake or an external registry cache unless
multiple images/platforms or ephemeral builders make the extra policy useful.

Remaining gate: authenticate Docker to the real Zot instance, publish one clean
commit, and retain its printed digest for the checkout-free update. The registry
address remains operator environment, not repository configuration.

### Checkout-free Compose update

The self-contained [`deploy/data/dev/`](../../deploy/data/dev/) bundle now has
no build directives, source mounts, or checkout-relative paths. Its `control`
helper persists one digest-pinned selection and provides full `reconcile`,
application-only `update`/`rollback`, `preflight`, and `status` operations.
FastText model downloads survive replacements in the `fasttext-cache` volume.

Local rendering proves every application role uses the same pinned image, with
zero build/source inputs. An isolated control-state test proved first reconcile,
schema-before-replacement, replacement of only `code-location`, `server-worker`,
and `operator-web`, failed-preflight diagnosis, 0600 image state, and rollback.
The host does not build or require repository contents.

Remaining gate: install the bundle on DEV, reconcile the first real Zot digest,
confirm the running revision, then perform one source-only update and rollback
without restarting RabbitMQ, Caddy, Dagster webserver, or Dagster daemon.

### Persistent DEV acceptance

1. Preflight reports every dependency `ok` without displaying credentials.
2. Dagster UI loads through `<gateway>/dagster` and shows the code location.
3. Operator workbench loads through `<gateway>/operator`.
4. Run `canonicalization_campaign` against a real bounded corpus slice.
5. Confirm canonical rows and the committed product head appear in the
   workbench with a link to the producing Dagster run.
6. Restart Dagster webserver and code location; confirm run history persists.
7. Cause or select a failed rerun and confirm the preceding successful product
   head remains visible rather than appearing rolled back.
8. Confirm the code location reads bronze, writes only to the DEV DuckLake
   destination, and reaches the AniList service gateway.
9. Confirm no container can write to bronze and no application container has a
   source mount or repository dependency.
10. Deploy an ordinary source change and confirm RabbitMQ, Caddy, Dagster
    webserver, and Dagster daemon do not restart.
11. Confirm the deployed digest matches an immutable Git tag in Zot and that a
    rollback preserves Dagster and domain state.

Gate: record the deployed image size, transferred bytes for a source-only
update, service interruption, preflight result, and rollback time. E2.2 begins
only after explicit acceptance.

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

The proposed first vertical slice uses subtitle LID as a lightweight transport
canary; its unapproved design and sign-off questions are in
[`lakehouse-subtitle-lid-local-worker.md`](lakehouse-subtitle-lid-local-worker.md).

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

After the prerequisite is accepted, E2.5 is the final production cutover proof
rather than another deployment design pass.

1. Run canonicalization and native-worker acceptance exclusively through the
   public campaign application service.
2. Compare product rows/fingerprints with the prior compiler outputs.
3. Inventory and delete the remaining proof-only executor, storage, scripts,
   variables, and fixtures.
4. Record the exact production prerequisites and rollback boundary.

Gate: a code/template change restarts only replaceable DEV services; no
supported route or CLI needs proof-era execution code; Dagster loss does not
make domain products uninterpretable; explicit sign-off authorizes production.
