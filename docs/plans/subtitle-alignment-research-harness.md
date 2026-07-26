# Scrappy subtitle-alignment research harness

Status: proposed experiment, not a product pipeline or campaign.

## Recommendation

Create a disposable `research/subtitle-alignment/` environment that consumes one
frozen Silver canonicalization snapshot, caches the relevant subtitle bytes,
runs timing experiments locally, and records manual review labels.

It should answer four questions before alignment becomes a Dagster product:

1. How many Kitsunekko candidates already fit, need a constant shift, show
   multiplicative drift, or need local retiming?
2. Where work is needed, does ALASS or ffsubsync produce better subjective
   results, and does VAD evidence correlate with that result?
3. Where is the practical cutoff beyond which neither method helps?
4. Can a subtitle group and transform learned on one episode be reused across a
   series, or is per-episode/intra-episode work necessary?

This is a consumer of Silver, not a second pipeline. It adds no campaign, Gold
table, service, worker contract, or research-side orchestration framework. Pure
logic moves back to `packages/core` only after it works; a proven durable
computation may move to `packages/data` later with separate sign-off.

## Input boundary

The successful canonicalization run gives us a real baseline: 2,474 canonical
episodes and 3,929 embedded subtitle tracks across 138 AniList series. The
experiment should prove that these Dagster-produced intermediates are usable
without first designing another product layer.

```text
Silver snapshot (read only)
  canonical_episode_inputs + canonical_subtitle_inputs
  subtitle_language_results when available
          |
          | one snapshot-pinned, bounded export
          v
local dataset: manifest + DuckDB + content-addressed subtitle cache
          |
          +-- Kitsunekko inventory/content + Kitsunekko LID
          +-- timing methods + metrics + microbenchmarks
          +-- lazy audio/VAD for reviewed episodes only
          v
manual labels + CSV/Markdown reports
```

`packages/data` owns the source tables, snapshot identity, and existing
read-only DuckLake/bronze clients. The research harness owns only its sample,
cache, method runs, labels, and reports. The exporter records:

- canonical materialization ID and producing Dagster run;
- DuckLake snapshot ID;
- every selected canonical input ID and fingerprint;
- sample-selection config and exporter revision; and
- Kitsunekko mirror revision or inventory fingerprint before and after the
  read, retrying if it changes during enumeration.

The exporter performs no remote writes. After export, method evaluation and
reporting run offline. Kitsunekko objects are cached by SHA-256 so every method
sees identical bytes.

## Repository shape and size budget

Keep this outside the root uv workspace so experiment-only dependencies do not
expand the supported package surface:

```text
research/subtitle-alignment/
  README.md
  pyproject.toml
  src/alignment_research/
    cli.py          # snapshot, evaluate, review, report
    snapshot.py     # bounded Silver/Kitsunekko acquisition
    methods.py      # transform searches + ALASS/ffsubsync adapters
    metrics.py      # geometry and benchmark summaries
    review.py       # experiment-specific Textual screen
    report.py
  tests/fixtures/   # synthetic, redistributable cases
  .gitignore        # .cache/ and output/
```

This is a ceiling, not a requirement to create every file immediately. Keep the
first useful version below roughly 1,500 hand-written lines and every module
below 300. If that is impossible, stop and identify the missing reusable seam.

The existing subsync frontend already supplies the useful seams:

- `SubtitleTrack` and cached LID metadata;
- `render_candidate_table`;
- `TimelineWidget` candidate/reference spans;
- cue and track navigation, constant-offset nudging; and
- materialized-audio playback around a cue.

Do not pile research behavior into the existing 431-line `subsync/tui.py` or
322-line `subsync/service.py`; both already exceed the 300-line soft limit. If
implementation touches either, extract a coherent reusable review component and
leave the file smaller.

## Local storage and hardware

Use a single local DuckDB file for resumable joins, plus content-addressed
objects:

```text
.cache/<dataset-id>/
  manifest.json
  evaluation.duckdb
  objects/subtitles/<sha256>
  objects/audio/<canonical-id>.*       # lazy, review subset only
  derived/vad/<audio-fingerprint>.json
  derived/aligned/<run-id>/<method>/...
output/<run-id>/{summary.md,results.csv,benchmarks.csv}
```

The DuckDB relations cover selected episodes, canonical tracks, Kitsunekko
inventory/LID, method runs, metrics, and append-only manual judgments. They
store Silver identities as provenance rather than copying Dagster state or
rebuilding canonical tables.

Subtitle storage is cheap enough to cache in bulk. The canonical tracks are
about 80 MB and the relevant Kitsunekko candidates are estimated around 175 MB;
budget less than 500 MB. Full cue timelines are required to measure late drift
and coverage.

Do not bulk-cache audio. Subtitle-reference ALASS/ffsubsync and interval scoring
need no media. Fetch or materialize full episode audio only when an episode
enters subjective/VAD review, cache it by canonical fingerprint, and play short
windows from that file. Full audio is needed for detecting late drift even if
the reviewer usually listens to clips.

An ordinary laptop is enough: start with two concurrent processes on four CPU
cores, less than 1 GB for subtitle/results storage, and no GPU requirement for
Gates 0–2. A Metal/CUDA runtime is needed only if the chosen VAD backend requires
it; VAD is an ablation, not a prerequisite.

## Sample: 25 diverse series

Select 25 series by default with a recorded random seed and retain every
canonical episode and candidate for those series. Choose deterministically from
the frozen inventory:

- 3 full or near-full Kitsunekko series;
- 3 mixed-coverage series;
- 2 sparse/no-subtitle series;
- 2 with many candidates or release groups; and
- 2 long or structurally unusual series.

These strata may overlap; fill the remaining slots with a seeded random sample.
Force several `AT-X`-hinted candidates and matched non-`AT-X` controls. Include
complete series where possible for Gate 4. Record
the selection query and AniList IDs; do not select only easy cases after seeing
method results.

The seeded draw continues until 25 usable series are accepted. Reject a series
with only one canonical episode, or when more than 25% of its canonical
episodes lack a retrievable Kitsunekko candidate. Record every rejected series,
missing episode, and candidate fetch failure. Exactly 25% missing is allowed;
isolated episode holes remain part of the sample rather than being concealed.

## Gate 0: LID Kitsunekko first

Run the existing `analyze_subtitle_language` implementation on the exact cached
Kitsunekko bytes before alignment. Report Japanese, bilingual,
unknown/insufficient, non-Japanese, and parse/read failure separately.

The main comparison uses Japanese candidates and a separate bilingual stratum.
Unknown/failures remain visible for manual triage and coverage; non-Japanese is
excluded from Japanese ranking. Record LID config/version and content hash.
Never trust filename or declared-language hints alone—the embedded-track audit
already showed those can be wrong. Embedded non-Japanese tracks can still be
timing anchors.

## Gate 1: how much actually needs retiming?

Evaluate this transform ladder against each embedded timing anchor:

1. `identity`: raw Kitsunekko timing;
2. `constant`: `t' = t + b`;
3. `affine`: `t' = a*t + b`; and
4. `piecewise`: a small number of penalized constant/affine regions.

Use the existing ALASS-derived goodness-of-fit function to score proposed
transforms. It is deliberately first-class here. It does not infer shifts, so
the harness supplies bounded searches for `b`, then `(a, b)`, then a small
piecewise fit. Actual ALASS remains a separate Gate 2 method.

For each anchor/candidate pair record:

- raw and transformed fit, and gain over identity;
- offset, scale, region count, and window-to-window offset dispersion;
- median/mean/p90 activity-boundary error;
- cue-count, active-duration, and episode-span coverage ratios; and
- download/cache, parse, search/subprocess, total wall time, and peak RSS.

“Misalignment” here is a subtitle-activity geometry proxy, not semantic
cue-to-cue truth: embedded and candidate tracks can be different languages.
Manual audio review calibrates it in Gate 2.

Run cold and warm microbenchmarks on a fixed 100-pair stratum. Report
pairs/second and memory on the actual machine. Do not invent a performance
threshold before measuring it.

The Gate 1 report gives the percentage of eligible pairs and episodes that pass
unchanged, after constant shift, after affine retiming, only after piecewise
retiming, or never pass the provisional cutoff. Break it down by release/group
hints including `AT-X`. The hypothesis is whether `AT-X` predicts
identity/constant success; the string itself never grants a pass.

Gate 1 ends when those distributions and microbenchmarks exist and every bucket
has a small manual-review sample.

## Gate 2: ALASS versus ffsubsync

Run actual ALASS and ffsubsync on identical cached anchor/candidate pairs. Pin
versions and arguments, and preserve outputs/logs by run ID. Neither tool is the
default going in.

Subjective correctness against episode audio is primary:

- which output is best;
- whether it is acceptable untouched;
- whether errors are constant, drifting, or local/discontinuous; and
- whether the method damages regions that were already correct.

Metric fit is secondary evidence for review sampling and later cutoff
calibration.

For the audio subset, run the existing VAD abstraction once and measure how VAD
overlap correlates with raw/aligned timing and the human verdict. Compare these
policies independently:

1. constant offset;
2. whole-episode affine/multiplicative retiming;
3. a few penalized segment-level transforms;
4. span adjustment toward VAD without changing cue duration; and
5. cue/span clipping to VAD boundaries.

VAD clipping is last because it has already produced poor subjective results.
Policies 4–5 must not modify ALASS/ffsubsync baselines. Specifically review
clipped sentence onsets/offsets, destroyed lead-in/out, and jitter.

Gate 2 ends after a blinded shared review set compares both aligners and the
transform/VAD ablations. Subjective acceptance chooses the winner; runtime and
metric fit break close ties.

## Manual reviewer

Build one experiment screen around existing subsync abstractions. For the same
episode/time window it can show the embedded anchor, raw candidate,
identity/constant/affine variants, ALASS, ffsubsync, and optional VAD spans.

Reuse the candidate table, timeline/reference spans, navigation, playback, and
manual `z`/`x` nudging. Add only:

- A/B switching while holding the time window;
- playback before/through/after the selected cue;
- jumps to worst-fit and method-disagreement windows;
- best-method selection; and
- durable verdict/note saving without modifying source subtitles.

Minimum verdicts:

- `good_auto`;
- `acceptable_minor`;
- `bad_constant`;
- `bad_drift`;
- `bad_local`;
- `wrong_candidate`; and
- `unjudgeable`.

Also record the first failure timestamp and whether speech leads or lags the
subtitle. Labels include dataset/input hashes, method version/config, reviewer,
and timestamp. They are append-only; supersession is explicit.

Blind method names where practical. Review every disagreement plus stratified
samples of agreement, identity passes, affine/piecewise-only cases,
VAD-improved cases, and failures. Target 60–100 judgments, not every pair.

## Gate 3: set the “nothing helps” cutoff manually

Use manual labels to define a high-precision automation region and a failure
region, leaving an indeterminate middle:

- `auto_alignable`: a reviewed method passes the high-precision cutoff;
- `forced_alignment_candidate`: a Japanese track covers the episode, but
  subtitle-only methods are unacceptable;
- `retranscribe`: no usable Japanese track exists, or every candidate has a
  major content/coverage mismatch; and
- `indeterminate`: insufficient language, anchor, audio, or review evidence.

Choose the simplest cutoff whose metric features meet the desired precision for
`good_auto`. Report its confusion table and make every false accept reopenable
in the reviewer. Missing subtitles, parse failures, missing anchors, and LID
uncertainty remain categorical facts rather than being hidden in a low score.

Gate 3 ends when the cutoff and false-accept/false-reject costs are explicitly
accepted. It recommends policy; it still creates no campaign or Gold product.

## Gate 4: intra-episode versus series/group reuse

Normalize an inspectable `group_hint` from Kitsunekko path/filename while
retaining the raw value.

### 4a. Group coverage

For each `(AniList ID, group_hint)`, report episodes present / canonical
episodes, longest contiguous span, gaps/duplicates, Japanese-LID pass rate, and
embedded-anchor coverage. This says what percentage can support group reuse at
all.

### 4b. Does the first anchor choose the best group?

Use the earliest anchored episode only to rank groups, apply its winner to later
held-out episodes, and compare with the per-episode oracle. Report top-1 group
accuracy, held-out acceptance, and regret. The anchor episode never evaluates
itself. Repeat with a second early anchor to measure how quickly confidence
improves.

### 4c. Does a transform transfer within group/AniList?

Fit on the first anchored episode and apply unchanged to held-out episodes:

- shared constant offset;
- shared affine scale/offset; and
- shared segment transform only where coverage supports it.

Compare with per-episode constant, affine, and piecewise fits. Report pass rate,
residual error, and runtime saved. Also fit from only the first 5, 10, and 20
minutes, then evaluate against the complete episode. That directly measures how
much intra-episode evidence is needed.

Gate 4 ends when held-out results show whether group choice and transforms
generalize by coverage stratum. Drop segment-level reuse unless subjective
results clearly beat the simpler transforms.

## Development loop

Expose one private command with four verbs, not public `ja-media` commands:

```sh
cd research/subtitle-alignment
uv sync

uv run alignment-research snapshot --series-count 25 --seed 20260726
uv run alignment-research evaluate \
  --methods identity,constant,affine,alass,ffsubsync
uv run alignment-research review
uv run alignment-research report
```

Repository clients load credentials from their existing owning env/config
files; do not create another dotenv handoff for the user. `snapshot` may import
the data package for read-only catalog/bronze access. Audio acquisition is lazy.

Use synthetic fixtures for identity, offset, drift, discontinuity, missing
spans, and duplicate cues. Add one tiny local Docker-backed integration test
following the existing data-stack seed pattern: remote data may be read as a
bounded seed, but all writes happen locally.

## Outputs, cost, and promotion

Each Markdown report links its CSVs and clearly separates measured results from
assumptions. It includes source identities, sample/LID counts, Gate 1
distribution and microbenchmarks, Gate 2 manual preferences/VAD ablations, Gate
3 confusion table, and Gate 4 held-out group/transform results. The local
DuckDB file is experiment state, not a lakehouse product.

Maintenance is one standalone environment, a sub-500-MB subtitle cache, a
bounded audio cache, and no deployed process. A notebook would be smaller but
poor at resumable runs and durable review labels; putting this in Dagster now
would prematurely freeze unknown metrics; a generic evaluation platform would
defeat the experiment's purpose.

After the experiment:

- move proven interval search/transforms/metrics into `packages/core`;
- leave executable adapters with the runtime that owns their dependencies;
- move a computation into `packages/data` only when its grain, output contract,
  and update semantics are clear and it will be reused; and
- add a Dagster asset/campaign or Gold projection only after explicit sign-off.

Production code must never import `research/`.

## First implementation slice

Stop and inspect results after this slice:

1. create the standalone environment and cache;
2. snapshot 25 seeded series from one Silver snapshot plus Kitsunekko;
3. LID all cached Kitsunekko candidates;
4. evaluate identity and best constant offset with the existing ALASS-derived
   scorer;
5. produce the Gate 1 distribution and 100-pair microbenchmark; and
6. review raw versus constant-shift results in the reused subsync UI and save
   labels.

Only then add affine fitting, actual ALASS, ffsubsync, VAD, and group transfer,
in that order. This first slice proves the central claim: a Silver intermediate
produced by Dagster can feed a useful local experiment without a Gold layer or
a second orchestration system.
