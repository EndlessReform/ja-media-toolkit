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

## Measured smoke run

Seed `20260726` against the pinned DEV canonical head drew 29 series to accept
25. Four failed the 75% inventory-coverage gate. The resulting 67 MB local
dataset contains 501 canonical episodes, 838 embedded tracks, and 1,001 mapped
Kitsunekko candidates. Five advertised candidate objects returned HTTP errors;
after alternate candidates were considered, three accepted-series episodes had
no retrievable Kitsunekko subtitle. No accepted series was a singleton or
exceeded the 25% missing-episode limit.
