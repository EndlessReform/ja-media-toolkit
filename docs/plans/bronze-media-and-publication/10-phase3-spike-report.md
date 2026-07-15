# Phase 3 episode-resolution spike report

Status: implemented and exercised on 100 development captures. Resolver and
ledger evidence are retained; the tested conditional downstream asset model is
rejected and scheduled for removal.

## Recommendation

Keep the resolver, PostgreSQL ledger, diagnostics, and measured fixtures. Keep
Dagster only for a second proof using collection-level assets with internal
mapped tasks. Remove the optional `validated_episode_mapping` asset and do not
build a synchronized locator partition registry.

Dagster was useful for capture-partition execution, run history, and logs. The
spike did not prove that per-record software-defined assets are a good fit for
data-dependent filtering and capture-to-locator fan-in.

The tested flow is:

```text
Garage bronze manifest
  -> typed v1/v2 core contract
  -> PTN suggestion + explicit episode token
  -> exact AniList title/synonym + episode-count bounds
  -> PostgreSQL hint, accepted binding, or open issue
  -> Dagster episode_resolution materialization + visible check
```

The resolver executes in the local CLI or Dagster code-location process. Garage
retains bronze bytes, the AniList service supplies independent metadata, and the
development `ja_media_data` database owns durable hints, bindings, and issues.

## Acceptance policy tested

Automatic acceptance requires all of the following:

1. PTN returns one ordinary positive integer episode.
2. One explicit `EpNN` or `SxxEyy` token independently agrees.
3. PTN's parsed title exactly matches a normalized AniList romaji, English,
   native, or synonym title.
4. The episode is within AniList's reported episode count.
5. PostgreSQL uniqueness rules find no locator/capture overlap.

Ranges, fractions, missing episode tokens, title mismatch, out-of-bounds
episodes, malformed manifests, and uniqueness conflicts create open
`episode_resolution_issues`. Infrastructure failures still fail the run.

IDs incorporate capture, input ETag, recipe version, method, and candidate.
Rerunning identical evidence is idempotent. New evidence that reaches the same
capture/locator safely supersedes the prior immutable binding; a changed locator
is quarantined rather than silently corrected.

## Measured 100-capture result

The bounded development rollout produced:

| Outcome | Count |
| --- | ---: |
| Captures indexed | 100 |
| Hints | 96 |
| Accepted current bindings | 52 |
| Open issues | 48 |
| Filename title disagrees with AniList | 25 |
| Episode exceeds AniList count | 14 |
| Fraction/range/parser disagreement | 5 |
| No ordinary episode | 4 |

The failures were informative rather than random noise:

- AniList folder `10087` contains Fate/Zero episodes 1–25, but that AniList
  entry reports 13 episodes. Episodes 14–25 were rejected.
- AniList folder `101` mixes AIR, Air Gear, and the AIR movie. Exact title
  agreement rejected 25 Air Gear captures that episode bounds alone would have
  partly accepted.
- Bunny Drop contains fractional `2.5`, `3.5`, `6.5`, and `8.5` files. They
  remain review items under the ordinary integer episode policy.
- Movies, clean openings/endings, and extras correctly produced no ordinary
  episode binding.

These are measured corpus facts from the first lexicographic 100 committed
markers, not estimates of the whole corpus.

## With the grain

- Capture ID is a useful recomputation partition: one bad capture does not roll
  back 99 good decisions, and reruns are naturally idempotent.
- The UI/event log can show selected capture work, evidence metadata, and run
  history without making Dagster's database authoritative.
- PostgreSQL uniqueness and row locking are a better fit than orchestration
  metadata for current-binding invariants.

## Against the grain and contortions

- Dagster rejected an otherwise valid `dg.AssetExecutionContext` annotation
  when postponed annotations were enabled. The asset module needs eager,
  directly imported context types for decorator reflection.
- The deprecated `dagster asset materialize` command returned almost no useful
  success output and now points to `dg launch`; run/event inspection was needed
  to prove what executed.
- A blocking failed asset check turns a legitimate quarantine into a failed
  run. The implemented workaround used a non-blocking check plus optional
  `validated_episode_mapping`, but this made processed-and-rejected partitions
  look missing and is not retained.
- Legacy manifests have no explicit series object or capture ID. The typed core
  parser must use object-key context, and uncached discovery otherwise reads a
  manifest twice. The batch reader now reads each sampled legacy document once.
- Depending on `ja-media-core` for its small PTN contract also installs the
  package's audio/LID dependencies, including NumPy, SoundFile, and fastText.
  The code boundary is right; the packaging boundary is heavier than it should
  be.
- Resolution is naturally capture-partitioned, but downstream consumption is
  locator-keyed. The accepted locator is durable in PostgreSQL, while the
  current validated Dagster asset remains capture-partitioned. Dagster
  partition mappings do not derive this data-dependent many-to-one relationship
  from resolver output.
- Maintaining only accepted locators as another dynamic partition registry
  would duplicate PostgreSQL eligibility and repeat at later identity changes
  such as locator-to-media-pair and alignment-to-clips.
- Propagating capture partitions through later stages would schedule many
  useless no-op tasks. Neither approach is approved.

## Revised orchestration hypothesis

Use durable collection assets such as `episode_identity_ledger`,
`stable_episode_inventory`, `portable_audio`, `audio_language_results`,
`selected_media_pairs`, and `subtitle_alignments`. Inside each update, select a
bounded eligible delta from PostgreSQL and map typed tasks over it. PostgreSQL
stores item-level fingerprints and outcomes; Dagster supplies task retries,
logs, scheduling, and collection lineage.

This model should preserve the desired user experience: launch semantically
named work without copying opaque IDs between scripts, inspect individual task
failures, and retry without recomputing committed results.

## Developer inspection

From `packages/data`, after loading `.env`:

```sh
uv run ja-media-data resolve-sample --limit 100 --show issues
uv run ja-media-data resolve-sample --limit 100 --apply --show none
uv run ja-media-data resolution-report --limit 100
```

Dry-run is the default. `--apply` writes only the bounded selected slice to the
configured ledger. `resolution-report` is read-only and expands open issue
reason, stem, PTN result, explicit tokens, and full evidence.

## Next sign-off

Before calling the orchestration choice approved:

1. remove the rejected optional downstream asset/check while preserving the
   resolver and ledger;
2. prove one collection-level identity update with bounded mapped tasks and a
   partial-failure retry;
3. decide whether fractional/special episode locators are in scope or remain
   human-reviewed exceptions;
4. sample beyond the first lexicographic 100 to measure release-group and naming
   diversity; and
5. use the first `portable_audio` plus LID slice to compare Dagster's actual
   operator value with a PostgreSQL-backed CLI or Prefect flow.
