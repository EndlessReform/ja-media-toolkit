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
    arms on #summary.at("pair_count") anchor-candidate pairs drawn across the
    identity-score distribution. Every output is rescored by the repository's
    common ALASS-derived objective. The experiment writes normalized method and
    cue-grain products for a subsequent blinded annotator; it does not define an
    automatic acceptance cutoff.
  ],
  accepted: none,
)

= Question and boundary

The experiment asks which minimum transform class is worth subjective review:
identity, one global translation, one whole-file clock scale plus translation,
or penalized piecewise translations. Both external tools run as subprocesses
against an embedded subtitle timing anchor. Audio and VAD are absent from this
matrix, and no method clips cue boundaries to detected speech.

The cohort is selected once from identity-score deciles before any retimed
result is observed. All methods see the same staged bytes. The run used
#summary.at("workers") worker processes and completed in
#seconds(summary.at("elapsed_seconds")).

= Method matrix

The table is compiled directly from `method-definitions.csv`. `Scale` means a
whole-file clock/framerate correction; `Piecewise` means penalized changes in
translation between adjacent cue groups.

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
maximum score for the pair; it is not a human preference.

#figure(
  caption: [Custom ALASS-derived scorer results, generated from `method_summary` in DuckDB.],
  table(
    columns: (2.3fr, 0.7fr, 0.85fr, 0.85fr, 0.75fr, 0.75fr),
    align: (left, right, right, right, right, right),
    stroke: none,
    inset: (x: 3pt, y: 2.5pt),
    toprule,
    table.header([Method], [Scored], [Median score], [Median gain], [Improved], [Wins]),
    midrule,
    ..methods.map(row => (
      cell(row.at("method")),
      cell(integer(row.at("scored_pairs"))),
      cell(decimal(row.at("median_score"))),
      cell(decimal(row.at("median_gain"))),
      cell(percent(row.at("improved_fraction"))),
      cell(percent(row.at("win_fraction"))),
    )).flatten(),
    botrule,
  ),
) <score-table>

= Runtime and realized transforms

We infer the realized transform from input and output cue clocks rather than
assuming flags behaved identically. Offset blocks count adjacent cue regions
whose translations differ by more than 21 ms. External ALASS outputs beyond
the declared 30-second absolute offset bound remain inspectable but are marked
as bounded rejects.

#figure(
  caption: [Runtime and transform complexity, procedurally compiled from method runs.],
  table(
    columns: (2.35fr, 0.85fr, 0.85fr, 0.75fr, 0.75fr, 0.85fr),
    align: (left, right, right, right, right, right),
    stroke: none,
    inset: (x: 3pt, y: 2.5pt),
    toprule,
    table.header([Method], [Median ms], [p90 ms], [Blocks], [Scaled], [Max offset]),
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
    columns: (3.2fr, 0.65fr, 0.85fr, 0.65fr, 0.55fr),
    align: (left, right, right, right, right),
    stroke: none,
    inset: (x: 3pt, y: 2.5pt),
    toprule,
    table.header([Method], [Scored], [Transform gaps], [Rejected], [Failed]),
    midrule,
    ..methods.map(row => (
      cell(row.at("method")),
      cell(integer(row.at("scored_pairs"))),
      cell(integer(row.at("transform_errors"))),
      cell(integer(row.at("bounded_rejects"))),
      cell(integer(row.at("failed_pairs"))),
    )).flatten(),
    botrule,
  ),
) <status-table>

Aggregated by transform class, the same underlying run table yields:

#figure(
  caption: [Transform-class rollup. Multiple penalty arms contribute to the piecewise row.],
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

= Procedural interpretation

The method with the largest median scorer gain is
*#summary.at("best_median_gain_method")*, at
#decimal(str(summary.at("best_median_gain"))) over identity. This is a queueing
signal for annotation, not evidence of subjective superiority. The matrix
contains #summary.at("run_count") pair-method cells:
#summary.at("scored_runs") scored, #summary.at("bounded_rejects") bounded
rejects, and #summary.at("failed_runs") execution or parse failures. Of the
scored outputs, #summary.at("transform_errors") changed cue count and therefore
lack a cue-by-cue transform map while remaining available for review.

The scorer rewards interval agreement with the embedded anchor. It cannot tell
whether differently authored tracks preserve readable lead-in/out, whether a
local offset damages a previously correct scene, or whether the candidate is
semantically the right Japanese subtitle. Those questions belong to blinded
review.

= Annotator handoff

The paper is a view over the same durable products intended for the next UI:

- `review-queue.parquet`: one row per sampled pair with best method, score gain,
  method spread, decile, and staged input paths;
- `review-variants.parquet`: one row per pair-method output with scores,
  realized transform, status, and aligned artifact path;
- `cue-transforms.parquet`: cue-level source/aligned clocks, offsets, scales,
  and block boundaries; and
- `gate1-matrix.duckdb`: all source, result, summary, and review relations.

The annotator can therefore order work, switch variants, and jump to offset
boundaries without rerunning ALASS or ffsubsync. Human labels should join by
`pair_id` and `method` and remain append-only. LID gates candidates before any
policy decision; audio/VAD, duration-derived or continuous scaling, and joint
clock-plus-piecewise arms remain out of scope until blinded labels justify
them.
