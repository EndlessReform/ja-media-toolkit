# Scrappy subtitle-alignment research harness

Status: active experiment, not a product pipeline or campaign. The frozen Phase
0 sample and naive Gate 1 identity survey are complete; LID and executable
aligner comparisons remain pending.

## Recommendation

Continue using the disposable `research/subtitle-alignment/` environment to
consume one frozen Silver canonicalization snapshot, cache the relevant
subtitle bytes, run timing experiments locally, and record manual review
labels.

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

Gate 1 now has a measured identity baseline and should use the installed
aligners for the next transform probes instead of first inventing parallel
constant, affine, and piecewise optimizers.

### Completed baseline

The Phase 0 export accepted 25 series after 29 seeded draws. It contains 501
canonical episodes, 838 embedded timing anchors, and 996 cached Kitsunekko
candidates. The `identity-v3` survey evaluated every cached
anchor/candidate Cartesian pair without changing timestamps:

- 422 episodes had at least one pair and 421 produced a score: 84.0% of all
  canonical episodes;
- 1,400 of 1,436 pairs scored successfully;
- the all-pair median normalized anchor-fit score was 0.190, while the best
  pair per episode had a median of 0.620;
- 409 scored episodes had multiple anchors and 193 had multiple candidates;
  the episode-best result is therefore an optimistic ranking view; and
- no sampled filename/path carried an `AT-X` hint, so this random sample cannot
  answer the AT-X hypothesis. Add a separately declared targeted stratum.

The report and pair/episode/series tables live under the local
`gate1-identity-v3-<dataset-id>` result directory. These scores are not an
acceptance threshold. Manual review still has to establish what score and gain
mean perceptually.

### Confirmed executable transform surface

The installed tools are `alass-cli 2.0.0` and `ffsubsync 0.5.1`. Keep both
behind subprocess adapters with version, arguments, stdout/stderr, runtime, and
input/output hashes recorded. In particular, shell out to the GPL-3.0 ALASS
executable; do not import or link `alass-core` into repository packages. This is
the experiment's technical boundary, not a legal determination.

The surface below was checked against the installed `--help` output and pinned
upstream source: [ALASS at `874f02d`](https://github.com/kaegi/alass/tree/874f02d9577182752a0f969b6d6b98fd65bdf1fc)
and [ffsubsync 0.5.1 at `de310ac`](https://github.com/smacke/ffsubsync/tree/de310ac6944b8260431a48ee741e7063cec49b0f).

Neither aligner requires cue-edge clipping:

| Transform class | ALASS restriction | ffsubsync restriction | Cue effect |
|---|---|---|---|
| identity | existing measured control | existing measured control | none |
| global translation | `--no-split --disable-fps-guessing` | `--no-fix-framerate --skip-infer-framerate-ratio` | one offset; duration preserved |
| clock scale + translation | `--no-split` | `--skip-infer-framerate-ratio`; optionally `--gss` in a separate arm | whole timeline and cue durations scaled, then shifted |
| piecewise translation | `--disable-fps-guessing --split-penalty P` | `--no-fix-framerate --skip-infer-framerate-ratio --split-penalty P` | contiguous cue groups get different offsets; duration preserved |
| clock scale + piecewise translation | `--split-penalty P` | `--skip-infer-framerate-ratio --split-penalty P` | one whole-file scale, then penalized group offsets |

ALASS checks six common ratios among 23.976, 24, and 25 fps. Its split mode
computes an offset per cue but penalizes changes, so equal adjacent offsets form
blocks. The output scales each cue once for the selected clock ratio and then
translates it; it does not reshape individual cue boundaries.

ffsubsync normally compares a small set of common framerate ratios and may also
infer a scale from reference/candidate duration. Different subtitle tracks can
have different episode coverage, so duration-derived scale is a dangerous
confound here. Always pass `--skip-infer-framerate-ratio` except in an explicitly
named duration-inference ablation. `--no-fix-framerate` alone does not disable
that inference. Its experimental split mode likewise emits per-cue offsets with
a split penalty, after any selected whole-file scale, and preserves cue
durations within that clock.

Use an embedded subtitle file as the reference in Gate 1. That keeps the first
comparison subtitle-only and avoids both audio acquisition and VAD. The
language of the timing anchor need not match the candidate, although English
SDH density and Japanese dialogue density can differ; manual review must test
whether the anchored English track is a better timing signal than audio VAD.

### Bounded tuning matrix

Do not take a Cartesian product of every CLI flag. Run a fixed 100-pair
microbenchmark/review stratum in this order:

1. identity, already computed;
2. global translation only from each aligner;
3. each aligner's common-framerate scale plus global translation;
4. piecewise translation with scale disabled at split penalties `5`, `10`, and
   `20`; and
5. only if scaling and splitting each help independently, one joint
   scale-plus-piecewise arm at the best reviewed penalty.

For ffsubsync, bound every search with `--max-offset-seconds`; start at 30
seconds and add a separately labelled 60-second rescue arm only for failures.
ALASS has no corresponding CLI search bound, so the harness rejects rather than
trusts an output whose inferred absolute offset exceeds the declared bound.

Hold ALASS `--interval=1` and `--speed-optimization=1`, and ffsubsync
`--split-length-penalty=0.25` and `--split-subsample=1`, during the transform
comparison. They are accuracy/performance knobs, not new retiming classes. Tune
them only if a measured runtime or localization problem appears. Likewise:

- ffsubsync `--apply-offset-seconds` replays a known manual offset; it does not
  estimate one;
- `--skip-sync-on-low-quality` is useful only after human labels calibrate its
  unnormalized score and offset/scale thresholds; and
- `--suppress-output-if-offset-less-than` is a signed comparison, not an
  absolute-small-offset gate, so do not use it as the identity-pass rule.

This matrix gives us global translation, whole-episode multiplicative timing,
and a few penalized discontinuities without implementing “move and clip every
single anchor.” It also tells us whether a more flexible method improves a
human verdict or merely improves its own objective.

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

The next Gate 1 report gives the percentage of eligible pairs and episodes that
are acceptable unchanged, after global translation, after whole-file clock
scaling, only after piecewise translation, or under none of them. Break it down
by release/group hints including the separately declared `AT-X` stratum. The
hypothesis is whether `AT-X` predicts identity/global-translation success; the
string itself never grants a pass.

Gate 1 ends when those distributions and microbenchmarks exist and every bucket
has a small manual-review sample.

## Gate 2: ALASS versus ffsubsync

Use the Gate 1 outputs from identical cached anchor/candidate pairs for blinded
subjective comparison. Neither tool or transform class is the default going in;
the pinned versions, arguments, outputs, and logs already belong to the Gate 1
run record.

Subjective correctness against episode audio is primary:

- which output is best;
- whether it is acceptable untouched;
- whether errors are constant, drifting, or local/discontinuous; and
- whether the method damages regions that were already correct.

Metric fit is secondary evidence for review sampling and later cutoff
calibration.

For the audio subset, run the existing VAD abstraction once and measure how VAD
overlap correlates with raw/aligned timing and the human verdict. Also run
ffsubsync with audio as the reference for a controlled subset. Its normal VAD
path converts speech activity into a timeline used to estimate a global scale
and offsets; it does **not** clip every subtitle cue to detected speech. Compare:

1. embedded-subtitle reference with global translation;
2. embedded-subtitle reference with whole-file scale plus translation;
3. embedded-subtitle reference with penalized piecewise translation;
4. audio/VAD reference with the same restricted transform classes; and
5. VAD cue-edge clipping only as a small, explicitly labelled negative-control
   arm.

The principal VAD question is therefore whether it is a better reference signal
than the anchored embedded subtitle, not whether its spans should replace cue
boundaries. Keep the candidate's original start/end shape after alignment.
Evaluate clipping only because it has already produced poor subjective results
and we want measured confirmation, not because it is a proposed production
policy. Specifically review clipped sentence onsets/offsets, destroyed
lead-in/out, and jitter.

Start ffsubsync audio-reference comparisons with `subs_then_webrtc` and plain
`webrtc`; do not install a Torch/Silero stack merely to widen the first matrix.
The fused/Silero and Whisper-reference modes remain rescue arms if ordinary VAD
fails on reviewed examples. ALASS can also use a video/audio reference, but keep
that out of the first audio subset so the aligner comparison does not become a
VAD-implementation comparison at the same time.

Gate 2 ends after a blinded shared review set compares both aligners and the
transform/VAD ablations. Subjective acceptance chooses the winner; runtime and
metric fit break close ties.

## Manual reviewer

Build one experiment screen around existing subsync abstractions. For the same
episode/time window it can show the embedded anchor, raw candidate,
identity/global/clock-scaled/piecewise variants from ALASS and ffsubsync, and
optional VAD spans.

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
samples of agreement, identity passes, clock-scaled/piecewise-only cases,
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
  --methods identity,alass-global,ffsubsync-global
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

## Current implementation slice

Completed:

1. created the standalone environment and immutable local cache;
2. drew until 25 usable series were accepted from one Silver snapshot plus
   Kitsunekko, retaining all draw failures and episode holes;
3. cached 996 Kitsunekko candidates for 501 canonical episodes; and
4. evaluated and reported all 1,436 naive identity pairs with the existing
   ALASS-derived scorer, including parse failures and episode/series grains.

This already proves the central machinery claim: a Silver intermediate produced
by Dagster can feed a useful local experiment without a Gold layer or a second
orchestration system.

Stop and inspect results again after this next bounded slice:

1. LID all cached Kitsunekko candidates and retain every exclusion/failure in
   the denominator;
2. add subprocess adapters for the installed, version-pinned `alass-cli` and
   `ffsubsync` commands without importing either implementation;
3. run the global-translation-only restrictions for both tools on the fixed
   100-pair stratum;
4. add the common-framerate and three split-penalty arms only in the staged
   order above;
5. infer and persist the actual output transform from input/output cue clocks,
   including scale, median offset, number of offset blocks, maximum absolute
   offset, runtime, and failures; and
6. review raw versus method outputs in the reused subsync UI and save labels.

Do not add audio/VAD, cue-edge clipping, group transfer, or a home-grown affine
or piecewise optimizer in this slice. The installed executables already expose
the transform classes needed to learn whether those abstractions are useful.
