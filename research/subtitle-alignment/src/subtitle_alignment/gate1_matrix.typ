#import "@preview/bloated-neurips:0.8.0": botrule, midrule, neurips2026, toprule

#let summary = json("summary.json")
#let definitions = csv("method-definitions.csv", row-type: dictionary)
#let methods = csv("method-summary.csv", row-type: dictionary)
#let transforms = csv("transform-summary.csv", row-type: dictionary)

#let affls = (
  local: (
    department: "Subtitle alignment research",
    institution: "ja-media-toolkit",
    location: "Local experiment",
    country: "United States",
  ),
)
#let authors = (
  (name: "ja-media-toolkit", affl: "local", email: "local@invalid.example"),
)

#let cell(value) = [#value]
#let integer(value) = if value == "" { "-" } else { str(int(value)) }
#let decimal(value, digits: 3) = if value == "" {
  "-"
} else {
  str(calc.round(float(value), digits: digits))
}
#let percent(value) = if value == "" {
  "-"
} else {
  str(calc.round(100 * float(value), digits: 1)) + "%"
}
#let boolean(value) = if value == "true" { "yes" } else { "no" }
#let seconds(value) = str(calc.round(float(value), digits: 2)) + " s"

#show: neurips2026.with(
  title: [Gate 1 subtitle retiming matrix],
  authors: (authors, affls),
  keywords: ("subtitle alignment", "retiming", "ALASS", "ffsubsync"),
  abstract: [
    We evaluate #summary.at("method_count") deliberately restricted retiming
    arms on #summary.at("pair_count") anchor-candidate pairs spanning
    #summary.at("series_count") AniList series and the identity-score
    distribution. Every output is rescored by the repository's
    common ALASS-derived objective. The experiment writes normalized method and
    cue-grain products consumed by the local annotator; it does not define an
    automatic acceptance cutoff.
  ],
  accepted: none,
)

= Question and boundary

The experiment asks which minimum transform class is worth subjective review:
identity, one global translation, or a penalty-5 piecewise translation. Both
external tools run as subprocesses
against an embedded subtitle timing anchor. Audio and VAD are absent from this
matrix, and no method clips cue boundaries to detected speech.

The cohort is selected once from identity-score deciles before any retimed
result is observed. The deterministic draw traverses deciles round-robin while
skipping represented series whenever any unseen series remains; only then does
it fill remaining slots with repeat series. All methods see the same staged
bytes. The run used
#summary.at("workers") worker processes and completed in
#seconds(summary.at("elapsed_seconds")).

= Method matrix

The table is compiled directly from `method-definitions.csv`. `Scale` means a
whole-file clock/framerate correction; `Piecewise` means penalized changes in
translation between adjacent cue groups.

`Penalty` is a cost per change of offset, not a time cutoff or piece limit.
With value $C = 5$, ALASS subtracts $min(n_a, n_c) C / 1000$ from its
interval-pair rating sum per change; $n_a$ and $n_c$ are the normalized nonzero
interval counts. ffsubsync instead subtracts $C$ seconds of overlap-equivalent
score per change (internally $100 C$ samples at 100 Hz). The nominal penalty is
therefore not an equal-strength regularizer across tools.

#figure(
  caption: [Restricted executable arms. Arguments are persisted separately in the same generated table.],
  table(
    columns: (2.3fr, 0.9fr, 1.25fr, 0.55fr, 0.7fr, 0.65fr),
    align: (left, left, left, center, center, right),
    stroke: none,
    inset: (x: 3pt, y: 2.5pt),
    toprule,
    table.header([Method], [Tool], [Transform], [Scale], [Piecewise], [Penalty]),
    midrule,
    ..definitions.map(row => (
      cell(row.at("key")),
      cell(row.at("tool")),
      cell(row.at("transform_class")),
      cell(boolean(row.at("scale_enabled"))),
      cell(boolean(row.at("piecewise_enabled"))),
      cell(if row.at("split_penalty") == "" { "-" } else { row.at("split_penalty") }),
    )).flatten(),
    botrule,
  ),
) <method-matrix>

= Uniform scorer outcomes

Every successfully parsed output is evaluated with
`subtitle_goodness_of_fit` and the normalized `subtitle_anchor_fit_score`.
Gain is relative to that pair's unchanged candidate. A win includes ties at the
maximum score for the pair; it is not a human preference. We report quartiles
and extremes rather than an arithmetic mean because a few catastrophic or easy
pairs can otherwise obscure the cohort's shape.

#figure(
  caption: [Score distributions from `method_summary`. Identity is retained as the unchanged-timing reference.],
  text(size: 7.5pt)[#table(
      columns: (2.0fr, 0.45fr, 0.65fr, 0.65fr, 0.65fr, 0.65fr, 0.65fr),
      align: (left, right, right, right, right, right, right),
      stroke: none,
      inset: (x: 2pt, y: 2.5pt),
      toprule,
      table.header([Method], [$n$], [Min], [$q_25$], [Median], [$q_75$], [Max]),
      midrule,
      ..methods.map(row => (
        cell(row.at("method")),
        cell(integer(row.at("scored_pairs"))),
        cell(decimal(row.at("min_score"))),
        cell(decimal(row.at("p25_score"))),
        cell(decimal(row.at("median_score"))),
        cell(decimal(row.at("p75_score"))),
        cell(decimal(row.at("max_score"))),
      )).flatten(),
      botrule,
    )],
) <score-table>

#figure(
  caption: [Gain over identity distributions and pair-level outcome fractions.],
  text(size: 7.5pt)[#table(
      columns: (2.0fr, 0.65fr, 0.65fr, 0.65fr, 0.65fr, 0.65fr, 0.75fr, 0.65fr),
      align: (left, right, right, right, right, right, right, right),
      stroke: none,
      inset: (x: 2pt, y: 2.5pt),
      toprule,
      table.header([Method], [Min], [$q_25$], [Median], [$q_75$], [Max], [Improved], [Wins]),
      midrule,
      ..methods.map(row => (
        cell(row.at("method")),
        cell(decimal(row.at("min_gain"))),
        cell(decimal(row.at("p25_gain"))),
        cell(decimal(row.at("median_gain"))),
        cell(decimal(row.at("p75_gain"))),
        cell(decimal(row.at("max_gain"))),
        cell(percent(row.at("improved_fraction"))),
        cell(percent(row.at("win_fraction"))),
      )).flatten(),
      botrule,
    )],
) <gain-table>

= Runtime and realized transforms

We infer the realized transform from input and output cue clocks rather than
assuming flags behaved identically. Offset blocks count adjacent cue regions
whose translations differ by more than 21 ms. The maximum offset is the
largest absolute translation applied to any individual candidate cue; it is
not accumulated drift. Because ALASS cannot bound its search, a cue translated
more than 30 seconds receives a diagnostic flag but remains scored. Large
negative translations can clamp early cues at zero, making one nominal global
translation appear as several realized offset blocks.

#figure(
  caption: [Runtime and transform complexity, procedurally compiled from method runs.],
  table(
    columns: (2.35fr, 0.85fr, 0.85fr, 0.75fr, 0.75fr, 0.85fr),
    align: (left, right, right, right, right, right),
    stroke: none,
    inset: (x: 3pt, y: 2.5pt),
    toprule,
    table.header([Method], [Median ms], [p90 ms], [Blocks], [Scaled], [Max cue shift]),
    midrule,
    ..methods.map(row => (
      cell(row.at("method")),
      cell(decimal(row.at("median_runtime_ms"), digits: 1)),
      cell(decimal(row.at("p90_runtime_ms"), digits: 1)),
      cell(decimal(row.at("median_offset_blocks"), digits: 1)),
      cell(percent(row.at("scaled_fraction"))),
      cell(decimal(row.at("max_abs_offset_s"), digits: 1)),
    )).flatten(),
    botrule,
  ),
) <runtime-table>

#figure(
  caption: [Method status accounting. Transform gaps are scoreable outputs whose cue counts changed during rewriting.],
  table(
    columns: (3.0fr, 0.65fr, 0.85fr, 0.85fr, 0.55fr),
    align: (left, right, right, right, right),
    stroke: none,
    inset: (x: 3pt, y: 2.5pt),
    toprule,
    table.header([Method], [Scored], [Transform gaps], [>30 s cue shift], [Failed]),
    midrule,
    ..methods.map(row => (
      cell(row.at("method")),
      cell(integer(row.at("scored_pairs"))),
      cell(integer(row.at("transform_errors"))),
      cell(integer(row.at("offset_bound_flags"))),
      cell(integer(row.at("failed_pairs"))),
    )).flatten(),
    botrule,
  ),
) <status-table>

Aggregated by transform class, the same underlying run table yields:

#figure(
  caption: [Transform-class rollup. Both tools contribute to each non-identity row.],
  table(
    columns: (1.5fr, 0.7fr, 0.8fr, 0.9fr, 0.9fr, 0.8fr),
    align: (left, right, right, right, right, right),
    stroke: none,
    inset: (x: 3pt, y: 2.5pt),
    toprule,
    table.header([Class], [Methods], [Runs], [Median score], [Median gain], [Blocks]),
    midrule,
    ..transforms.map(row => (
      cell(row.at("transform_class")),
      cell(integer(row.at("methods"))),
      cell(integer(row.at("scored_runs"))),
      cell(decimal(row.at("median_score"))),
      cell(decimal(row.at("median_gain"))),
      cell(decimal(row.at("median_blocks"), digits: 1)),
    )).flatten(),
    botrule,
  ),
) <transform-table>

= Artifact contract

The paper and local UI consume the same durable products; corpus interpretation
remains in separately versioned notes:

- `review-queue.parquet`: one row per sampled pair with best method, score gain,
  method spread, decile, and staged input paths;
- `review-variants.parquet`: one row per pair-method output with scores,
  realized transform, status, and aligned artifact path;
- `cue-transforms.parquet`: cue-level source/aligned clocks, offsets, scales,
  and block boundaries; and
- `gate1-matrix.duckdb`: all source, result, summary, and review relations.
