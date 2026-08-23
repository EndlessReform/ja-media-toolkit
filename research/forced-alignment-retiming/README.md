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

The slice maps each review source to its own forced-alignment result. Audio is
resolved for the selected AniList series and episode. `f` and `F` move among
cleaning flags and suspicious alignments; cue playback uses the retimed borders.

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
