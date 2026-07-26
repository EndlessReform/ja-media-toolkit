# Subtitle-alignment research

This is a deliberately disposable consumer of the canonical Silver product.
It is a normal uv workspace project so imports and dependencies are resolved by
uv; it does not modify `sys.path` or duplicate package source.

Phase 0 proves that a local experiment can:

1. pin the current `canonical_inputs` materialization and DuckLake snapshot;
2. draw AniList series in reproducible random order until 25 usable series pass;
3. retain every canonical episode and embedded-subtitle locator in that sample;
4. fetch each Kitsunekko series inventory once through the configured core SDK;
5. cache every Kitsunekko candidate mapped to a selected canonical episode; and
6. leave a local DuckDB dataset that later alignment experiments can query.

## Run Phase 0

From this directory:

```sh
uv sync
uv run alignment-research snapshot --series-count 25 --seed 20260726
```

> **DO NOT MERGE — temporary Bronze v1 escape hatch:** `snapshot` currently
> restricts its Silver draw pool to AniList IDs in the gitignored
> `DELETETHIS-subs-only-anilist-ids.txt`. It fails closed when that file is
> missing or empty. Delete the gate in `snapshot.py`, its helper/test, and this
> note when Bronze v2 is ready.

The command automatically uses the repository's existing DEV read credentials
and the personal `[services].root_url` configuration. It does not require a new
environment file and does not print secrets or the resolved tailnet URL.

The resulting `.cache/<dataset-id>/` contains:

- `manifest.json`: exact Silver/Kitsunekko revisions and counts;
- `evaluation.duckdb`: episode, embedded-track, and Kitsunekko-candidate rows;
- `objects/embedded/<sha256>`: normalized UTF-8 embedded subtitle content; and
- `objects/kitsunekko/<sha256>`: exact bytes returned by Kitsunekko.

The cache is gitignored. A snapshot is immutable: rerunning the same dataset
refuses to overwrite it.

A usable series has at least two canonical episodes and a retrievable
Kitsunekko candidate for at least 75% of them. The sampler records rejected
series and keeps drawing until the requested total is reached. Missing episodes
and advertised candidate IDs whose content returns an HTTP failure remain
explicit rows; one-off holes do not invalidate an otherwise usable series.

Inspect the local result without reconnecting to DEV:

```sh
uv run alignment-research inspect .cache/<dataset-id>
```

Phase 0 does not run LID or alignment. Those computations consume this frozen
dataset in later gates.

## Run the Gate 1 identity baseline

This scores unchanged timing only. It does not search offsets or implement any
retiming model:

```sh
uv run alignment-research identity \
  .cache/phase0-1345e0a2045b031e --workers 8
```

The command writes pair, episode, and series result tables under `output/`,
renders two PNG charts, writes a procedural Markdown report, and compiles the
report to the repository's `output/pdf/` directory with Pandoc and XeLaTeX.

## Run the Gate 1 executable matrix

The matrix draws 100 pairs evenly across identity-score deciles, executes the
restricted ALASS and ffsubsync arms, and rescores every output with the same
repository-owned ALASS-derived scorer:

```sh
uv run alignment-research matrix \
  .cache/phase0-1345e0a2045b031e \
  --identity-result output/gate1-identity-v3-phase0-1345e0a2045b031e \
  --sample-size 100 --workers 8
```

Results include normalized DuckDB, CSV, and Parquet tables plus staged input,
aligned-output, and log artifacts. `review-queue.parquet`,
`review-variants.parquet`, and `cue-transforms.parquet` are the handoff to the
annotator UI. The generated `gate1-matrix-paper.typ` reads its tables directly
from the CSV/JSON products and compiles with
`@preview/bloated-neurips:0.8.0`; rerun `typst compile` in the result directory
after changing the paper source or generated tables.

The `>30 s cue shift` column is a diagnostic count, not drift and not a failed
run. It means that reconstructing the output clocks found at least one
individual candidate cue translated by more than 30 seconds. Matrix-v2 showed
that the largest values came from sparse signs/song tracks being used as
full-episode anchors. Those outputs remain scored so they can be inspected.

## Inspect flagged pairs

The local annotator consumes the DuckDB and staged subtitles above and does not
rerun ALASS/ffsubsync. Run this command from either the repository root or this
research directory; relative result paths are resolved in both places:

```sh
uv run alignment-research annotate \
  output/gate1-matrix-v3-phase0-1345e0a2045b031e-n100
```

Flagged pairs appear first and are the default filter. Pass `--all-pairs` to
include the rest. A persistent left rail selects and locates `AniList:episode`;
the main view separates method choices, the shared subsync timeline, current
cue, and status rather than flattening them into one header.

- `h` / `l`: previous / next cue, keeping it visible
- `v`: show the complete raw anchor and candidate side by side; `Tab` switches
  the independently scrollable panes and `Esc` closes the modal
- `j` / `k`: next / previous method output
- `[` / `]`: previous / next episode; `,` / `.` changes candidate pair
- `Ctrl-f` / `Ctrl-b`, `Ctrl-d` / `Ctrl-u`: page or half-page the timeline
- `+` / `-`, `gg` / `G`: zoom or jump to the start/end
- `A`: lazily fetch and decode indexed derived audio for the selected episode
- `Space`: play/stop the selected cue through subsync's existing audio player
- `1` / `2` / `3` / `4`: append `anchor_usable`, `anchor_sparse`,
  `candidate_mismatch`, or `needs_audio`
- `q`: quit

Labels append to `RESULT/annotations.jsonl` by default. They are diagnostic
input-cohort labels, not subjective alignment verdicts; audio-backed blinded
review comes after sparse anchors are removed from the method comparison. Audio
is never fetched on startup or episode navigation. `A` first uses subsync's
indexed anime-audio cache. If that artifact does not exist, it follows the
Phase 0 episode's pinned manifest and caches only that episode's immutable
bronze audio under `.cache/<dataset>/objects/audio/`. Both paths use subsync's
full-file PCM materialization and sounddevice player; subsequent loads reuse the
local compressed artifact.

## Measured smoke run

Seed `20260726` against the pinned DEV canonical head drew 29 series to accept
25. Four failed the 75% inventory-coverage gate. The resulting 67 MB local
dataset contains 501 canonical episodes, 838 embedded tracks, and 1,001 mapped
Kitsunekko candidates. Five advertised candidate objects returned HTTP errors;
after alternate candidates were considered, three accepted-series episodes had
no retrievable Kitsunekko subtitle. No accepted series was a singleton or
exceeded the 25% missing-episode limit.
