# Forced-alignment retiming research

This is the bounded experiment described in
`docs/plans/forced-alignment/cleaned-retiming-first-slice.md`. It is private
research code, not a public `ja-media` command or a Dagster asset.

The first command resolves one named case through the latest committed
`canonical_inputs` product. It downloads that episode's pinned audio and
embedded subtitles, gates and ranks only that episode's Kitsunekko candidates,
and writes the chosen pre-cleaning source:

```sh
uv run --project research/forced-alignment-retiming \
  retiming-research pair arakawa-bridge-09
```

Results go to `output/<case>/candidate-ranking.json`, with the fetched inputs
beside it. The manifest records the exact Silver materialization, the score
breakdown, and a hash for each downloaded file. Re-running the same case is
safe; identical files are reused.

`--cases`, `--output-root`, and `--data-config` accept explicit paths. The
default case file is this directory's `cases.toml`, and the default data config
is `packages/data/config.dev.toml`.

## Full cleaned slice

The 2026-08-22 campaign aligned 25 unique source subtitles against 12 episode-1
audio files. The cleaning run contains 26 catalog IDs, but two Arakawa IDs point
to the same source bytes; the campaign keeps one deterministic catalog owner for
that source. Across the 25 results, 8,630 cues were reconstructed from 1,106
model windows. Every input cue has a selected timing. The present structural
rules label 2,714 cues suspicious; that is a review queue, not an absent-text
probability.

Start the review UI with the whole alignment slice:

```sh
cd packages/frontend
uv run ja-media-srt-clean review \
  --run-dir ../../output/srt-clean/corpus-slice-2026-08-22/luna-openrouter.reconstruct \
  --alignment-case ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/slice.json \
  --episode 1
```

The slice maps each review source to its own forced-alignment result and its
downloaded `audio.relative_path`. Initial load and source or episode changes use
that prepared local audio. The derived-audio service is only the fallback for a
source without a prepared alignment; an explicit `--audio` still takes priority.
`f` and `F` move among cleaning flags and suspicious alignments. F5 starts on
the cues and borders written to each case's `full-alignment/retimed.srt`; press
`t` to switch both the timeline and Space playback between that materialized
subtitle and the source subtitle. A source cue removed before alignment is absent
from the aligned timeline and is labelled as such in the cue panel.

## Preparation and concurrency

The cleaning-to-alignment join can be rebuilt without hand-writing 25 cases:

```sh
cd research/forced-alignment-retiming
uv run retiming-research prepare-slice \
  ../../output/srt-clean/corpus-slice-2026-08-22/luna-openrouter.reconstruct \
  --episode 1 \
  --output-root output/corpus-slice-2026-08-22
```

`prepare-slice` groups identical source bytes, binds each selected catalog ID to
its own LLM reconstruction, and fails on missing canonical audio, source hash
changes, or a cue reconstruction mismatch.

Full alignment now defaults to 32 simultaneous windows. A cache-clean sweep over
120 distinct crops measured 28.58 requests/s at 32 and 29.35 requests/s at 47.
Thus 32 retained 97.4% of measured peak throughput while keeping median request
latency at 1.06 seconds instead of 1.48 seconds. The campaign did not reduce
concurrency after errors.

Original subtitle cues beyond the canonical audio duration are sent with the
final audio core rather than dropped. This lets the model produce edge, entropy,
repetition, and ordering metrics for likely edition mismatches or text absent
from the audio.

## Crop-edge experiment

The 2026-08-23 experiment asks a narrow question: does moving the same dialogue
near the beginning or end of the audio sent to Qwen change its predicted cue
borders? It uses 36 cues:

- 12 human-reviewed ordinary-dialogue cues whose saved interior candidate had
  broken timestamp order;
- 12 ordinary-dialogue cues with clean saved timings, matched by source when
  possible and then by text/cue duration; and
- 12 selector conflicts where the saved winner had broken timestamp order but
  another saved candidate was ordered. Full Metal Panic cue 81 is included.

Every cue is sent in six test versions: 60 and 180 seconds, with the complete
source cue placed 2 seconds from the beginning, centered, or 2 seconds from the
end. That produces 216 requests. Preparation also writes a row for every saved
candidate timing, so the corpus-wide audio-edge and text-list-edge rates can be
checked separately from the controlled placements.

Prepare the input-pinned inventory and 36-cue target manifest without calling
the aligner:

```sh
cd envs/inference
uv run qwen3-retime-case edge \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/slice.json \
  --reviewed-targets ../../research/forced-alignment-retiming/edge-dialogue-targets.json \
  --edge-stage prepare
```

Run the 216 requests. `--base-url` may be omitted when a default
`[forced_alignment]` backend is present in the normal toolkit config:

```sh
uv run qwen3-retime-case edge \
  ../../research/forced-alignment-retiming/output/corpus-slice-2026-08-22/slice.json \
  --reviewed-targets ../../research/forced-alignment-retiming/edge-dialogue-targets.json \
  --edge-stage run \
  --concurrency 32 \
  --base-url <forced-alignment-adapter-url>
```

The aggregate results, paired beginning/end-versus-middle comparisons, target
manifest, candidate inventory, and compact inventory summary live under
`edge-experiment/`. Each participating case also gets
`stability/results.json`, which is what the review UI reads.

Start the review UI with the full slice using the earlier command, move to a
participating subtitle source with `j`/`k`, and press `F7`. The comparison shows
the six exact playback intervals, movement from the same-length middle result,
timestamp-order status, and the closest predicted border to the crop edge.
Press `1`–`6` to hear a test version, `o` to hear the original subtitle borders,
and `j`/`k` inside the dialog to move through that source's tested cues.

### Current findings

The corpus inventory contains 17,493 saved candidate timings. Among strict
ordinary-dialogue candidates, broken timestamp order falls from 85.1% within
0.2 seconds of a predicted crop edge to 24.3% more than 10 seconds away. That is
an uncontrolled corpus association; the six-placement test checks the same cue
under controlled crop changes.

Two input-identical passes of the 216-request placement test found:

- among 70–72 beginning/end results whose same-length middle result was
  ordered, 22–25 became broken and 35–38 either became broken or moved a border
  by more than 0.5 seconds;
- beginning placement was worse than end placement: 14–15 versus 8–10 newly
  broken results;
- 180-second tests produced 29 border shifts over 10 seconds, versus 13 for
  60-second tests; and
- seven returned cue intervals extended beyond the audio sent to the aligner.

The input-identical repeat changed an exact cue border in 4 of 216 results, with
3 moving by more than 0.16 seconds, and changed timestamp-order status in 2.
That jitter changes a few threshold counts but is much smaller than the crop
placement effect. These findings establish that crop position affects this
aligner and that 2 seconds is unsafe as a production clearance. They do not, by
themselves, establish that 60 seconds is universally better than 180 seconds;
they do show substantially more extreme movement in the tested 180-second
placements.
