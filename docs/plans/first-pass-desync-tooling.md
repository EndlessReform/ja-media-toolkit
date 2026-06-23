# First-Pass Subtitle Desync Tooling

Build a Textual-first subtitle desync tool for answering:

> Which subtitle timing source matches this media file, and can I repair it with
> a simple retime?

The interface starts as a terminal app, not an HTML report or desktop editor.
The command-line layer exists for scripting the same operations the TUI performs.

## Goals

- Rank one or more subtitle candidates against a local media file.
- Estimate whether a candidate needs no fix, a constant offset, or likely drift.
- Apply safe constant-offset retiming to `.srt` files.
- Provide a Textual review flow for candidate selection, manual nudging, and
  safe `.srt` export.
- Produce a machine-readable JSON report for repeatability and scripting.
- Reuse `AudioChunk`, `SpeechSpan`, `VadTimeline`, `VadBackend`, and
  `VadOptions` instead of inventing a separate audio-analysis layer.

## Non-Goals

- No HTML report in the first pass.
- No desktop GUI or browser-based review tool.
- No forced alignment in v1.
- No hand-rolled VAD or audio decoder.
- No subtitle typesetting, translation, or text-editing workflow.
- No Windows support or platform abstraction beyond the repo's existing
  `envs/*` boundaries.

## First-Class Interface

The first interface should be a Textual app:

```sh
cd envs/apple
uv run ja-media subsync tui ../../media/episode.mkv ../../subs/*.srt
```

The initial screen should be useful after one analysis run:

```text
episode.mkv

Candidates
1  Some.Group.E07.ja.srt   constant_offset  0.91  +0.450s  high
2  Other.Group.E07.ja.srt  weak_match       0.63  +83.20s  low
3  Some.Group.E08.ja.srt   bad_candidate    0.24  n/a      low

Selected
Verdict: shift all cues by +0.450s
Drift: none detected
Action: [enter] apply  [j/k] select  [h/l] nudge  [w] write  [q] quit
```

The CLI commands should use the same library functions and report objects:

```sh
uv run ja-media subsync diagnose ../../media/episode.mkv ../../subs/*.srt \
  --json-out ../../reports/episode.subsync.json

uv run ja-media subsync apply ../../subs/episode.ja.srt \
  --offset-s 0.450 \
  --out ../../subs/episode.ja.synced.srt

uv run ja-media subsync apply \
  --from-report ../../reports/episode.subsync.json \
  --candidate 1 \
  --out ../../subs/episode.ja.synced.srt
```

The non-interactive diagnosis command should print a compact table:

```text
media: episode.mkv

rank  candidate                 verdict          score  offset    drift
1     Some.Group.E07.ja.srt      constant_offset  0.91   +0.450s   none
2     Other.Group.E07.ja.srt     weak_match       0.63   +83.20s   unclear
3     Some.Group.E08.ja.srt      bad_candidate    0.24   n/a       n/a

recommended: apply +0.450s to Some.Group.E07.ja.srt
```

### Current Manual Review Shell

The first landed interface is a manual timing-review TUI:

```sh
cd envs/apple
uv run ja-media subsync tui ../../media/episode.mkv '../../subs/*.srt'
```

Implemented behavior:

- resolves a source media file and one or more `.srt` paths or quoted globs;
- parses `.srt` cues into runtime-free `SubtitleCue` values in
  `ja_media_core.transcripts`;
- lists candidates with current cue, total cue count, active subtitle time, and
  total subtitle span;
- renders the selected candidate's subtitle activity as a wide Textual timeline
  using colored half-block cue spans and blank gaps;
- shows the currently selected subtitle text;
- supports `h/l` for previous/next cue, `j/k` for candidate selection,
  `gg/G` for start/end, Vim-style page and half-page movement, and `+/-` zoom;
- binds `space` to exact-boundary playback of the current cue from decoded
  in-memory PCM;
- stops active playback when moving between cues with `h/l` or quitting.

The current shell intentionally does not score candidates, infer offsets, nudge
timing, or write repaired sidecars yet. It is a listening/review surface that
exercises the durable SRT parsing contract and establishes the Textual layout.

## Repo Fit

The repo keeps the shared boundary deliberately small:

- `packages/core` owns durable, runtime-free media, VAD, and transcript
  contracts plus lightweight subtitle parsing and formatting.
- `envs/apple` should own the first runnable command because the available VAD
  implementation is `MlxAudioVadBackend`.

Do not add model dependencies to `packages/core`. Transcript helpers should
operate on plain timing intervals and report objects. The environment command should
materialize media, run VAD, call the transcript/scoring library, and write
outputs.

## Core Data Model

Keep the first model deliberately small.

`SubtitleCue`

- source file path
- cue index
- start seconds
- end seconds
- text
- optional metadata for format-specific fields

`TimingCandidate`

- candidate path
- cue intervals
- parser metadata

`TimingDiagnosis`

- candidate path
- score
- verdict
- best offset in seconds, if any
- drift estimate, if measured
- confidence
- summary metrics

`SubsyncReport`

- media path
- VAD backend name and options
- audio speech spans, or a reference to a cache file
- ranked candidate diagnoses
- recommended action
- command/config metadata needed to reproduce the run

For audio-derived timing, use existing `SpeechSpan` values from `VadTimeline`.
Subtitle cues can be converted into the same simple interval shape for scoring,
but they should not pretend to be VAD output.

## Implementation Plan

### Phase 1: Textual Shell, SRT Parsing, And Safe Retiming

Home:

- pure parsing/retiming in `ja_media_core.transcripts`
- Textual app and command wiring in `envs/apple`

Build:

- [x] Add a `subsync tui` command that opens a Textual app.
- [x] Parse `.srt` into `SubtitleCue` values.
- [x] Write `.srt` while preserving cue text and ordering.
- [x] Shift cue timestamps by a constant offset.
- [x] Clamp or reject negative timestamps with explicit behavior.
- [x] Let the TUI load media/subtitle paths and list candidates.
- [x] Add tests for parsing, formatting, and offset application.
- [ ] Accept a manual offset in the TUI.
- [ ] Preview an output path and write a new sidecar file from the TUI.
- [ ] Add overwrite-safety tests for sidecar export.

This gives an immediately useful terminal workflow before automatic scoring:
open candidates, apply a known offset, and write the corrected SRT safely.

The first usable slice is slightly narrower: open candidates, inspect timing
activity, navigate cues, and listen to exact cue boundaries. Manual offset and
safe sidecar export remain the next Phase 1 work.

### Phase 2: VAD-Based Candidate Scoring In The TUI

Home:

- pure scoring in `ja_media_core.transcripts`
- VAD execution and Textual integration in `envs/apple`

Workflow:

1. Resolve/probe the media file into an `AudioChunk`.
2. Run `MlxAudioVadBackend.detect(...)` with `VadOptions`.
3. Parse each candidate SRT into cue intervals.
4. Rasterize audio speech and subtitle activity into fixed-step binary signals.
5. Cross-correlate over a bounded lag range.
6. Rank candidates by best overlap score and peak confidence.
7. Update the Textual candidate list with score, verdict, offset, and
   confidence.
8. Write `SubsyncReport` JSON on request, and let the CLI print the same table
   in non-interactive mode.

Good defaults:

- sample interval for activity scoring: 20 ms to 50 ms
- maximum offset search: configurable, default around 120 s
- minimum speech per scoring window: enough to avoid OP/ED/silence-only lies
- VAD options: reuse the repo defaults unless the CLI passes overrides

The scoring code should accept intervals and numbers only. It should not import
`ja_media_apple`, MLX, ffmpeg wrappers, or concrete VAD backends.

### Phase 3: Drift And Local Evidence

Add local windows only after global offset ranking works.

Build:

- split the media timeline into windows, for example 60 s with 30 s hop
- estimate local offset per usable window
- reject windows with too little speech or weak correlation
- fit a simple line to local offsets
- classify candidates as:
  - `good_match`
  - `constant_offset`
  - `possible_drift`
  - `weak_match`
  - `bad_candidate`

Keep this heuristic at first. The useful thing is not mathematical perfection;
it is a reliable warning that "this is probably not one simple shift."

### Phase 4: Review Ergonomics

Improve the Textual app as the diagnostics become richer. It should stay a
review and accept/apply surface, not an editor:

- candidate list with score, verdict, offset, and drift warning
- small text timeline of VAD versus subtitle activity
- jump to worst local windows
- manual nudge controls
- accept detected correction
- write corrected SRT

Textual/Rich is the likely Python choice unless the repo has already committed
to another TUI stack by then.

## Report Format

Prefer JSON first:

```json
{
  "media_path": "episode.mkv",
  "vad": {
    "backend": "mlx-audio",
    "options": {
      "min_speech_s": 0.25,
      "min_silence_s": 0.2,
      "speech_pad_s": 0.05
    }
  },
  "candidates": [
    {
      "path": "Some.Group.E07.ja.srt",
      "rank": 1,
      "verdict": "constant_offset",
      "score": 0.91,
      "offset_s": 0.45,
      "confidence": "high",
      "recommended_action": "shift"
    }
  ]
}
```

Add a readable `.txt` summary if useful. Defer HTML until there is enough
evidence that terminal output and JSON are insufficient.

## Metrics To Start With

Start with a small set:

- overlap score after best offset
- best offset in seconds
- peak sharpness or second-best-gap confidence
- unmatched audio speech duration
- unmatched subtitle-active duration
- number of usable local windows
- local offset standard deviation, once local windows exist
- drift slope, once local windows exist

Avoid building a big metrics taxonomy before there are real files to calibrate
against.

## Safety Rules

- Never overwrite subtitle files unless the user passes an explicit overwrite
  flag.
- Default corrected subtitle names should be new sidecars.
- Keep dry-run output useful enough to inspect the exact target path and timing
  transform.
- Preserve cue text exactly when applying timing-only changes.
- Store enough metadata in reports to know which VAD options and candidate file
  produced a recommendation.

## Future Work

These are useful, but not part of the first pass:

- `.ass` support through a real subtitle parser such as `pysubs2`
- ASR timestamp quality diagnostics using existing `AsrTranscript` /
  `AsrSegment` output
- diarization segment comparison
- batch season/source-group comparison
- piecewise retiming
- forced alignment for line-level repairs

Forced alignment should be a second-stage tool: first get a coarse, VAD-based
answer about whether the timing source belongs to the media at all.
