# Cleaned Subtitle Retiming: First Slice

Status: approved direction, bounded implementation proposal. This document
defines the first experiment only. It does not approve a Dagster product,
automatic publication, or a general subtitle-normalization system.

## Recommendation

Start with a cheap subtitle-only funnel, select one base Kitsunekko candidate,
clean and review its dialogue text, then forced-align that one candidate across
the complete episode.

```text
canonical episode + embedded timing anchor + Kitsunekko inventory
  -> categorical candidate gate
  -> ALASS-derived anchor-fit ranking
  -> one selected base candidate
  -> reviewed cleaned dialogue
  -> full-episode forced alignment
  -> new retimed SRT + diagnostics
```

Forced alignment is a transformation in this slice, not a candidate-ranking
fan-out. A later slice may test alignment-derived rejection or confidence
signals, but only after the selected-candidate path proves useful.

## Problem Being Solved

Kitsunekko commonly offers several files for one episode. Cheap checks can
discard obvious junk, and the existing ALASS-derived interval score has been
useful for ranking the survivors. The selected file still needs cleanup before
alignment because it may contain effects, signs, notices, commentary, karaoke,
or ASS debris. The first useful end-to-end question is:

> Can one cheaply selected and human-reviewed cleaned candidate be transformed
> into a useful newly timed subtitle across a complete episode?

## Approved Decisions

1. Canonical bindings provide normal identity/audio; explicit local paths are
   the ad hoc escape hatch.
2. Candidate selection precedes cleanup and alignment: rule out obvious junk,
   then rank the survivors by an ALASS-derived anchor-fit score.
3. Excess candidate cue count receives no penalty until it exceeds the anchor
   by more than one third; beyond that point a mild multiplier applies.
4. The top-ranked eligible candidate is selected immediately. Forced alignment
   does not fan out across every candidate.
5. Cleanup is mandatory and reviewed before alignment, which creates a new
   dialogue-only retimed SRT without mutating its Kitsunekko source.
6. Dagster integration waits until the five-case slice produces useful
    results and recognizable failure patterns.

## Repository Facts

- `subtitle_goodness_of_fit` supplies the ALASS-derived interval value.
- `subtitle_anchor_fit_score` adds active-duration and cue-count penalties. Its
  cue penalty starts at any excess, so the one-third deadband replaces that
  term rather than stacking another penalty.
- Embedded tracks proved useful for rough ranking, not fine speech boundaries.
- SRT cleaning already records keep, edit, remove, and escalate decisions.
- Qwen3 alignment works on supplied audio, but episode window planning and cue
  projection remain unimplemented.

## Scope And Cases

Use five canonical episodes:

1. an ordinary, clean-looking SRT;
2. a candidate with a substantial constant offset;
3. a candidate with late drift;
4. messy ASS containing signs, songs, formatting, or commentary; and
5. the best available candidate still suspected to be poor.

Cover roughly three series. Canonical cases name an AniList locator and episode
plus optional selection overrides:

```toml
[[cases]]
locator = "anilist:15451:1"
initial_offset_s = 0.0

[[cases]]
locator = "anilist:15451:6"
subtitle_id = "optional-explicit-kitsunekko-id"
initial_offset_s = -12.4
```

Without `subtitle_id`, the funnel selects the candidate. An explicit subtitle
ID bypasses ranking but not validity checks.

The ad hoc escape hatch is deliberately small:

```toml
[[cases]]
audio_path = "/path/to/episode.mkv"
subtitle_path = "/path/to/candidate.ass"
anchor_path = "/path/to/reference.srt"
initial_offset_s = 0.0
```

## Candidate Funnel

### Categorical gate

Rule out a candidate when any of the following is true:

- subtitle download fails;
- parsing fails;
- the file has no positive-duration cues;
- language analysis classifies it as non-Japanese;
- language analysis reports insufficient text;
- its episode mapping does not match the requested canonical episode; or
- it is an unsupported subtitle format.

Japanese and bilingual candidates are eligible. Unknown language remains
visible but cannot win while a Japanese or bilingual candidate survives.

Coverage, duplicate density, short active duration, and suspicious group hints
are warnings, not new rejection thresholds.

### Anchor gate

Ranking requires one usable embedded timing anchor. Choose the densest
parseable embedded track that:

- has at least 100 positive-duration cues;
- begins within the first 20 percent of episode duration; and
- ends after 80 percent of episode duration.

These blunt criteria reject sparse signs/song anchors. Without a usable anchor,
retain the eligible pool and require a manual choice. Change the thresholds
only in response to an inspected case.

### Ranking score

Let:

- `V` be `subtitle_goodness_of_fit(anchor, candidate)`;
- `A` be the number of positive-duration anchor cues;
- `D_anchor` and `D_candidate` be raw active durations; and
- `r = C_candidate / C_anchor` be the positive-duration cue-count ratio.

Keep the existing active-duration multiplier:

```text
active_multiplier = min(1, D_anchor / D_candidate)
```

with multiplier `1` when the candidate duration does not exceed the anchor.

Replace the current immediate cue-count penalty with a one-third deadband:

```text
cue_multiplier = 1                              when r <= 4/3
cue_multiplier = sqrt((4/3) / r)               when r > 4/3
```

The initial selection score is:

```text
selection_score = max(0, V) / A
                  * active_multiplier
                  * cue_multiplier
```

This is a ranking heuristic, not an acceptance probability. Sort descending by
score, then stable subtitle ID, and select the first candidate.

Update the existing helper rather than adding a wrapper penalty. Update its
tests and displayed subsync scores with the same definition.

## Cleanup Gate

Run the selected candidate through the existing SRT-cleaning workflow.

For this slice, cleanup should:

- retain spoken Japanese;
- remove sound-effect descriptions and non-spoken signs;
- remove legal text, credits, translator notes, and release commentary;
- drop opening and ending karaoke;
- remove ASS formatting debris; and
- escalate ambiguous dialogue rather than freely rewriting it.

Review every edit, removal, and escalation. Record decision counts, reviewer
corrections, and review time.

Stop if accurate spoken text requires cue-by-cue rewriting. That is a cleanup
failure, not an aligner failure.

The accepted cleaned SRT remains on the source clock. Removed non-dialogue cues
do not enter the first retimed product.

## Alignment Recipe

Process cleaned cues in source order:

1. Group adjacent cues covering at most approximately 15 source-clock seconds.
2. Apply the case's coarse `initial_offset_s` when locating audio.
3. Extract no more than 30 seconds of audio around the group.
4. Tokenize with the existing Nagisa policy.
5. Call the existing Qwen3 vLLM adapter.
6. Translate returned window-relative timestamps to episode time.
7. Merge token timings into cue start and end times.

Do not add VAD, automatic global-offset search, rolling correction, or multiple
search strategies yet.

Allow one bounded retry when a group fails or concentrates timestamps at a
window edge. Retry once in the neighboring 30-second window indicated by the
edge. If that also fails, mark the group unresolved and continue.

## Local Outputs

Each case writes:

```text
output/<case>/
  candidate-ranking.json
  source.srt
  cleaned.srt
  cleanup-decisions.jsonl
  windows.jsonl
  alignments.jsonl
  retimed.srt
  unresolved-cues.jsonl
  summary.md
```

`candidate-ranking.json` includes every advertised candidate, categorical gate
result, warnings, score components, final score, and selected subtitle ID.

`retimed.srt` is a new dialogue-only artifact. The original candidate and
embedded anchor remain unchanged.

## Diagnostics And Review

Report the ranked pool, cleanup counts, aligned/unresolved/suspicious cues,
invalid or edge-pinned timestamps, retries, displacement distribution and
jumps, cues outside the episode, and runtime. Listen to ordinary early, middle,
and late regions plus every suspicious region. Record: good, slightly
early/late, wrong boundary, wrong speech, missing dialogue, hallucinated
alignment, cleanup error, or cannot judge.

## Implementation Shape

Keep the experiment disposable:

```text
research/forced-alignment-retiming/
  pyproject.toml
  cases.toml
  probe.py
  report.py
  tests/
```

Call the existing Qwen adapter directly. Tests cover gating/ranking, the cue
deadband, grouping, offsets and coordinate conversion, cue projection, the
single retry, timestamp extraction with a small hard-coded logits array, and
SRT reconstruction without source mutation.

## Execution Order

1. Update and test the anchor-fit cue-count multiplier.
2. Implement categorical gating and ranking without calling Qwen.
3. Inspect the selected candidate and score breakdown for all five cases.
4. Clean and review the ordinary case, prove alignment on a short excerpt, then
   run its complete episode and inspect runtime and failures.
5. Continue through the remaining four cases only if that result is promising.
6. Summarize cleanup burden, ranking behavior, alignment quality, runtime, and
   recurring failure patterns.

## Exit Criteria

Proceed to a second slice when:

- the cheap funnel selects plausible base candidates or clearly requests a
  manual choice when it lacks a usable anchor;
- cleanup is manageable on most cases;
- full-episode alignment works on more than the cleanest case;
- bad regions can usually be located from diagnostics;
- runtime is practical; and
- retimed subtitles materially improve manual review.

Stop or redesign when:

- the ranking repeatedly promotes obvious junk;
- cleanup becomes episode-specific rewriting;
- the model silently aligns absent text without recognizable instability;
- modest source-clock errors routinely place speech outside recoverable
  windows;
- unresolved regions are too common; or
- reviewing the output costs as much as manual retiming.

## Deferred Work

Defer forced-alignment ranking fan-out, numeric auto-acceptance, confidence
calibration, VAD checks, series-level reuse, signs/song retiming, Dagster and
DuckLake integration, automatic publication, and additional aligner backends.
