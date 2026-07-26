"""Charts, procedural conclusions, and Pandoc PDF for identity scoring."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import subprocess

import duckdb
import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402


def write_identity_report(result: Path, *, pdf_root: Path) -> Path:
    """Render charts and compile the measured identity survey with Pandoc."""

    summary = json.loads((result / "summary.json").read_text())
    database = result / "gate1-identity.duckdb"
    score_chart = result / "identity-score-distributions.png"
    series_chart = result / "identity-series-survey.png"
    _score_chart(database, score_chart)
    _series_chart(database, series_chart)
    markdown = result / "gate1-identity-baseline.md"
    markdown.write_text(_markdown(summary, score_chart.name, series_chart.name))
    pdf_root.mkdir(parents=True, exist_ok=True)
    pdf = pdf_root / (
        f"gate1-{summary['survey_version']}-{summary['dataset_id']}.pdf"
    )
    subprocess.run(
        [
            "pandoc", markdown.name, "--from=markdown", "--toc",
            "--pdf-engine=xelatex", "-V", "geometry:margin=0.75in",
            "-V", "fontsize=10pt", "-V", "colorlinks=true",
            "-o", str(pdf),
        ],
        cwd=result,
        check=True,
    )
    return pdf


def _score_chart(database: Path, output: Path) -> None:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        pair_scores = [row[0] for row in connection.execute(
            """SELECT anchor_fit_score FROM identity_pair_scores
                WHERE status = 'scored' ORDER BY anchor_fit_score"""
        ).fetchall()]
        episode_scores = [row[0] for row in connection.execute(
            "SELECT best_score FROM identity_episode_best ORDER BY best_score"
        ).fetchall()]
    finally:
        connection.close()
    figure, axes = plt.subplots(1, 2, figsize=(11.5, 4.3), constrained_layout=True)
    upper = max(1.0, max(pair_scores + episode_scores, default=1.0))
    bins = [upper * index / 40 for index in range(41)]
    axes[0].hist(pair_scores, bins=bins, color="#33658A", edgecolor="white")
    axes[0].set(title="All anchor-candidate pairs", xlabel="Identity anchor-fit score", ylabel="Pairs")
    axes[1].hist(episode_scores, bins=bins, color="#F28E2B", edgecolor="white")
    axes[1].set(title="Best identity pair per episode", xlabel="Best identity anchor-fit score", ylabel="Episodes")
    for axis in axes:
        axis.grid(axis="y", alpha=0.25)
        axis.set_xlim(0, upper)
    figure.suptitle("Identity baseline score distributions", fontweight="bold")
    figure.savefig(output, dpi=200)
    plt.close(figure)


def _series_chart(database: Path, output: Path) -> None:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        rows = connection.execute(
            """SELECT anilist_id, comparable_fraction, median_best_score,
                      scored_episodes, total_episodes
                 FROM identity_series_summary
                ORDER BY median_best_score NULLS FIRST, anilist_id"""
        ).fetchall()
    finally:
        connection.close()
    labels = [str(row[0]) for row in rows]
    coverage = [float(row[1]) for row in rows]
    medians = [float(row[2]) if row[2] is not None else 0.0 for row in rows]
    y = list(range(len(rows)))
    figure, axes = plt.subplots(1, 2, figsize=(11.5, 8.5), sharey=True, constrained_layout=True)
    axes[0].barh(y, coverage, color="#59A14F")
    axes[0].set(xlim=(0, 1), xlabel="Comparable episodes / canonical episodes", ylabel="AniList ID")
    axes[1].scatter(medians, y, color="#F28E2B", s=32)
    axes[1].set(xlim=(0, max(1.0, max(medians, default=1.0))), xlabel="Median episode-best identity score")
    axes[0].set_yticks(y, labels)
    for index, row in enumerate(rows):
        axes[0].text(min(0.98, coverage[index]), index, f" {row[3]}/{row[4]}", va="center", fontsize=7)
    for axis in axes:
        axis.grid(axis="x", alpha=0.25)
    figure.suptitle("Series coverage and identity fit", fontweight="bold")
    figure.savefig(output, dpi=200)
    plt.close(figure)


def _markdown(summary: dict[str, object], score_chart: str, series_chart: str) -> str:
    pair_q = summary["pair_score_quantiles"]
    episode_q = summary["episode_best_quantiles"]
    total_episodes = int(summary["total_episodes"])
    pair_episodes = int(summary["episodes_with_pairs"])
    scored_episodes = int(summary["episodes_with_scored_pairs"])
    total_pairs = int(summary["total_pairs"])
    scored_pairs = int(summary["scored_pairs"])
    failed_pairs = int(summary["failed_pairs"])
    zero_pairs = int(summary["zero_score_pairs"])
    coverage = scored_episodes / total_episodes if total_episodes else 0
    at_x_count = int(summary["at_x_scored_pairs"])
    at_x_median = _number(summary["at_x_pair_median"])
    other_median = _number(summary["other_pair_median"])
    at_x_section = _at_x_section(at_x_count, at_x_median, other_median)
    review_step = (
        "Review a stratified set across episode-best score deciles, exact-zero "
        "cases, and episodes with multiple anchors or candidates. Add a separate "
        "targeted AT-X stratum because this seeded sample contains none."
        if at_x_count == 0
        else "Review a stratified set across episode-best score deciles, "
        "exact-zero cases, method ambiguity, and AT-X vs non-AT-X hints."
    )
    return f"""---
title: "Gate 1 survey: naive subtitle identity baseline"
date: "{date.today().isoformat()}"
---

## Scope

This report evaluates the unchanged timing of every cached embedded-anchor x
Kitsunekko-candidate pair in Phase 0 dataset `{summary['dataset_id']}`. It calls
the existing `subtitle_goodness_of_fit` and `subtitle_anchor_fit_score`
functions with no proposed shifts.

No constant offset, affine transform, piecewise transform, ALASS execution,
ffsubsync execution, VAD, language filtering, or pass cutoff is implemented or
implied here. The result is an identity baseline, not an alignment verdict.

## Corpus and execution

| Measure | Value |
|---|---:|
| Canonical episodes | {total_episodes:,} |
| Episodes with at least one anchor-candidate pair | {pair_episodes:,} |
| Episodes with at least one scored pair | {scored_episodes:,} ({coverage:.1%}) |
| Episodes with no identity pair | {int(summary['episodes_without_pairs']):,} |
| Pair-bearing episodes blocked entirely by parsing | {int(summary['episodes_blocked_by_parsing']):,} |
| Anchor-candidate pairs | {total_pairs:,} |
| Successfully scored pairs | {scored_pairs:,} |
| Pair rows blocked by parsing | {failed_pairs:,} |
| Unique malformed embedded anchors | {int(summary['failed_anchor_tracks']):,} |
| Unique malformed Kitsunekko candidates | {int(summary['failed_candidate_tracks']):,} |
| Workers | {summary['workers']} |
| Wall time | {float(summary['elapsed_seconds']):.2f} s |

The unit of parallel work was one episode. Each process parsed every anchor and
candidate for that episode once, then evaluated its Cartesian pair set. This is
coarser and less repetitive than a row-wise dataframe apply while preserving
one output row per pair.

## Score distribution

![Pair and episode-best identity distributions]({score_chart}){{ width=95% }}

| Population | p10 | p25 | median | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| All scored pairs | {_q(pair_q, 0)} | {_q(pair_q, 1)} | {_q(pair_q, 2)} | {_q(pair_q, 3)} | {_q(pair_q, 4)} |
| Best pair per episode | {_q(episode_q, 0)} | {_q(episode_q, 1)} | {_q(episode_q, 2)} | {_q(episode_q, 3)} | {_q(episode_q, 4)} |

There are {zero_pairs:,} exact-zero pair scores ({zero_pairs / max(1, scored_pairs):.1%}
of scored pairs). Selecting the best identity pair per episode shifts the
distribution upward, as expected when episodes often have multiple embedded
tracks or Kitsunekko candidates. It is an optimistic ranking view, not evidence
that the selected subtitle is subjectively synchronized.

## Series view

![Series coverage and median episode-best identity fit]({series_chart}){{ width=95% }}

Series differ both in whether an identity comparison is possible and in their
best observed score. Coverage and score must remain separate: a high series
median says nothing about episodes with no anchor/candidate pair.

## AT-X descriptive slice

{at_x_section}

## Procedural conclusions

1. **The Silver intermediate is directly usable.** A frozen local snapshot was
   sufficient to enumerate and score all identity pairs without a Gold product,
   new service, or Dagster modification.
2. **Identity scoring now supplies a ranking baseline, not an automation
   threshold.** Pair and episode-best distributions are materially different;
   future decisions must state which grain they use.
3. **Multiplicity is common.** {int(summary['multi_anchor_episodes']):,} scored
   episodes have multiple anchors and {int(summary['multi_candidate_episodes']):,}
   have multiple candidates. The episode-best view is deliberately optimistic.
4. **Parsing failures are explicit.** Failed rows stay in the result table with
   anchor/candidate attribution rather than disappearing from the denominator.
5. **The next operation is manual calibration.** {review_step} Record whether
   identity timing is actually acceptable.
6. **Do not implement retiming yet.** Constant, affine, and piecewise transforms
   remain undefined. Before adding them, inspect what ALASS, ffsubsync, and
   ffmpeg actually expose and decide whether each proposed transform describes
   a method input, a measured output, or merely a scoring probe.

## Reproducible outputs

- `identity-pairs.csv` and `identity-pairs.parquet`: one row per pair.
- `identity-episodes.csv` and `identity-episodes.parquet`: best identity score
  per comparable episode.
- `identity-series.csv` and `identity-series.parquet`: series coverage and
  episode-best summaries.
- `gate1-identity.duckdb`: all three tables plus the episode inventory.
- `summary.json`: corpus, scorer, runtime, and quantile facts used above.
"""


def _q(values: object, index: int) -> str:
    return f"{float(values[index]):.3f}"  # type: ignore[index]


def _number(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _at_x_section(count: int, at_x_median: str, other_median: str) -> str:
    if count == 0:
        return (
            "No scored pair in this seeded sample carried an `AT-X` filename or "
            "path hint. This sample therefore provides no evidence for or "
            "against the AT-X identity-timing hypothesis. Test it with a "
            "separately declared targeted stratum, not by changing this random "
            "sample after seeing its scores."
        )
    return (
        f"Filename/path matching found {count:,} scored pairs carrying an "
        f"`AT-X` hint. Their pair-level median was {at_x_median}; all other "
        f"scored pairs had a median of {other_median}. This is descriptive only; "
        "candidate multiplicity, series composition, and language confound it."
    )
