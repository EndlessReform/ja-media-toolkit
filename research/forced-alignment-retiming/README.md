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
