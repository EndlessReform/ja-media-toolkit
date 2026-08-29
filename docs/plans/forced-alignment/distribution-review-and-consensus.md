# Forced-Alignment Distribution Review And Consensus Campaign

Status: proposed experiment; no aggregation rule or permanent service boundary is
approved yet.

## Recommendation

Back up one step before choosing a consensus function. Capture Qwen's timestamp
distributions for a bounded, deliberately stratified sample, inspect them as
ordered time distributions, and learn which compact representation preserves the
features that matter. Use a separate server-side web workbench for this review
rather than adding more modes to the subtitle-cleaning TUI.

Once the review establishes useful visual and quantitative intuitions, run one
five-placement campaign whose saved outputs can be replayed through several
candidate-level and distribution-level aggregators without repeating model
inference.

The immediate questions are:

1. Do full 5,000-bin endpoint distributions expose useful uncertainty structure
   that max probability, top-two margin, and normalized entropy hide?
2. Does top-k 64 preserve that structure well enough when accompanied by retained
   probability mass and a coarse full-range histogram?
3. Which distribution shapes correspond to stable borders, crop-position
   failures, wrong spoken occurrences, absent text, or otherwise bad alignments?
4. Can a model-review fanout using GPT-5.6 Luna identify the same recurring shapes
   and disagreements as the human reviewer?
5. Only after those questions: which aggregation family deserves full-slice human
   comparison?

## Current Repository Facts

- The forced-aligner checkpoint emits 5,000 timestamp classes at an 80 ms step for
  every start and end slot.
- The colocated adapter already receives fp16 timestamp rows selected by vLLM's
  `StepPool`.
- The adapter currently reduces each row to argmax, maximum probability, top-two
  margin, and normalized entropy, then discards the raw row.
- The full-alignment runner chooses one saved core or boundary candidate. It does
  not combine repeated predictions.
- The crop-position campaign showed that identical dialogue can move or become
  structurally invalid when its position inside a crop changes.
- The existing full-slice artifacts contain uneven candidate counts and do not
  contain raw distributions. New inference is required for systematic five-way
  comparison.
- The server already caches source audio by content hash and can decode exact
  source-clock crops.

## Why The Full Distribution May Matter

Timestamp is an ordered axis, not an arbitrary 5,000-class label set. Two rows can
have the same entropy, maximum probability, and top-two margin while having very
different useful shapes:

- one broad peak around a plausible border;
- two narrow peaks at competing spoken occurrences;
- a sharp peak plus a long tail;
- repeated peaks at rhythmic intervals;
- a broad, nearly flat region;
- a spike at the beginning or end of the crop; or
- start and end rows whose plausible regions contradict each other.

The existing summary cannot distinguish those cases. Full rows can. It is not yet
known whether those distinctions predict human acceptance or improve aggregation.
That is the first campaign's measurement target.

## Full Rows Versus Top-K 64

Top-k 64 is potentially a good storage and rendering representation for sharp or
moderately multimodal rows. It is unsafe by itself for diffuse rows because it can
hide most of the probability mass and erase the geometry of the tail.

For every reviewed endpoint, derive all representations from the same full row:

- full fp16 logits or probabilities;
- top 64 class indexes and probabilities;
- total probability mass retained by the top 64;
- probability mass outside the top 64;
- a coarse histogram made by summing the full 5,000 bins into 250 or 500 ordered
  time buckets;
- argmax, max probability, top-two margin, and full normalized entropy;
- the smallest contiguous interval around the argmax containing selected mass
  levels such as 50%, 80%, and 95%; and
- detected local peaks with their locations, heights, and separation.

The coarse histogram is important. It preserves full-range shape at small size
when top-k mass is low, and it makes a browser plot cheap without pretending that
the missing tail is unimportant.

Do not choose the permanent representation up front. The pilot should determine:

- how often top-k 64 retains most of the mass;
- whether top-k 64 preserves the number and location of meaningful modes;
- whether the coarse histogram exposes failures hidden by top-k 64;
- whether full rows change human or Luna classifications; and
- whether any full-row feature improves prediction of border agreement or human
  acceptance beyond the compact summary.

Full rows can be dropped from later campaigns only if those comparisons show that
the compact representation retains the useful distinctions.

## Representative Sampling

Entropy alone must not define the sample. It remains a useful axis, but the sample
must contain confident disagreements as well as uncertain failures.

Build a deterministic first review set containing:

- the 36 existing crop-position targets;
- the present/shifted/unrelated/inserted/nonspoken controls;
- low-entropy, high-agreement cues;
- low-entropy, low-agreement cues;
- high-entropy, high-agreement cues;
- high-entropy, low-agreement cues;
- rows with high and low top-k-64 retained mass;
- single-mode, multimode, broad, rhythmic, and crop-edge shapes;
- ordered and structurally invalid results;
- cues near text-list edges separately from audio-crop edges; and
- a bounded random sample across series, releases, cue lengths, and episode
  positions.

The first target is approximately 120 cues, balanced across the four
entropy-by-agreement cells and spread across the available series. The campaign
planner must write the exact selection rule, quantile boundaries, seed, and chosen
cue IDs. Human findings can then expand particular strata without rewriting the
original cohort.

For each selected cue, request five 60-second placements when episode geometry
allows it. The placements should move the cue through the crop while keeping the
text and audio source fixed. File-edge cues receive fewer valid placements and are
reported as a separate cohort rather than padded into fake symmetry.

## Visual Review Material

The main visualization is a token-by-time probability heatmap, referred to here as
the triangle plot. Render start and end slots in token order over the crop's time
axis, with:

- probability color on a logarithmic or clipped scale;
- token text and cue membership on the vertical axis;
- predicted start/end traces;
- crop boundaries and source subtitle borders;
- the target cue highlighted;
- local time and episode time available as a toggle; and
- identical color normalization across the five placements.

The cue detail page should also provide:

- a one-dimensional full distribution for the selected endpoint;
- a top-k-64 overlay and retained-mass label;
- the coarse histogram overlay;
- detected modes and mass intervals;
- synchronized audio playback around each predicted interval;
- candidate borders over a waveform or compact spectrogram;
- the source subtitle interval; and
- structural diagnostics for token order, crop bounds, and repeated timestamps.

The five placements should be viewable both side by side and projected onto one
episode-time axis. The latter view makes genuine agreement visible even though the
underlying distributions use different crop-local coordinates.

Static PNG versions of the plots must be materialized alongside the interactive
view. They are portable review material for Luna fanout and remain inspectable
without the web process.

## Luna Review Fanout

Luna is an additional reviewer, not an aggregation input during the discovery
phase. Give it deterministic review bundles containing the triangle plots,
one-dimensional endpoint plots, cue text, crop placement, compact metrics, and
candidate borders. Include audio only if the selected provider path supports the
required input without changing the cohort.

Use three independent responses per cue initially. Require structured output with:

- distribution shape labels;
- whether the five placements show one clear timing, competing timings, or no
  stable timing;
- which candidates belong to the dominant group;
- whether crop-edge attraction appears present;
- whether start and end distributions are mutually plausible;
- likely failure category;
- preferred candidate, if any; and
- a short diagnostic tied to visible plot features.

Save every response, provider/model identity, prompt hash, plot hashes, and input
cue fingerprint. Show human and Luna judgments side by side only after the human
label is recorded for that cue. Report Luna self-agreement and agreement with the
human labels by stratum; do not collapse three responses into an authoritative
label.

## Server-Side Review Workbench

Build this as a bounded research web application under the forced-alignment
research package. Do not add it to the shared Caddy gateway, Compose suite, or
first-party service catalog during the experiment.

Run it on the inference server bound to loopback. Access it through an SSH port
forward owned by the user. This avoids exposing a new LAN surface while placing
the process next to the large tensor and audio artifacts.

### Ownership Boundaries

- **vLLM:** model execution and fp16 pooling tensors.
- **Forced-alignment adapter:** source-audio cache, exact crop decode, and an
  explicit experimental raw-row capture path. It does not choose consensus.
- **Campaign runner:** window planning, request identity, tensor artifact writing,
  resumability, and derived compact representations.
- **Review workbench:** read-only browsing of campaign artifacts, plot rendering,
  clip materialization, and append-only human judgments.
- **Luna fanout:** independent structured review records joined by stable request
  identity.
- **Aggregation analysis:** offline consumers of the frozen campaign outputs and
  judgments. They must not mutate the source run.

### Pages

1. **Campaign overview**
   - completion and failure counts;
   - projected and actual tensor bytes;
   - entropy, retained-mass, edge-distance, and agreement distributions;
   - cohort and stratum filters.
2. **Sample queue**
   - deterministic review order;
   - filters for entropy, agreement, shape, edge position, series, and review
     state;
   - representative thumbnails.
3. **Cue detail**
   - five synchronized triangle plots;
   - endpoint distribution inspector;
   - common episode-clock comparison;
   - source and candidate playback;
   - human annotation form.
4. **Human/Luna comparison**
   - hidden Luna output until the human judgment is saved;
   - disagreement queue and per-stratum summaries.
5. **Aggregation playground**
   - read-only application of registered aggregation functions to the selected
     cue;
   - candidate membership, selected border, abstention reason, and sensitivity
     to one removed placement.

### Audio And Clip Cache

Campaign manifests hold content-hashed pointers to the source audio; the browser
never receives storage credentials or arbitrary filesystem paths.

On demand, the workbench materializes a short browser-playable clip around the
selected source or predicted interval. Cache keys include the source audio hash,
start/end times, channel policy, preprocessing arm, codec, and encoder settings.

Use two bounded recoverable caches:

- an LRU of at most two source episode files available to the workbench; and
- a clip cache bounded by both byte count and item count.

Eviction removes only derived local cache files. Campaign tensors, manifests,
plots, and judgments are not cache entries and are never evicted by the web app.

The clip endpoint accepts only registered cue/candidate identities. It must not be
an arbitrary path or arbitrary timestamp transcoding API.

## Campaign Artifacts

Use one immutable run directory per input fingerprint:

```text
distribution-campaign/
  manifest.json
  sample.jsonl
  requests.jsonl
  rows/
    <request-id>.fp16
    <request-id>.index.json
  compact/
    endpoints.parquet
    candidates.parquet
  plots/
    <cue-id>/<placement>.png
  luna/
    requests.jsonl
    responses.jsonl
    execution.json
  review/
    human-judgments.jsonl
  aggregation/
    <method-id>.parquet
    comparison.json
```

The index beside each tensor maps rows to token IDs, cue IDs, start/end slots,
crop-local coordinates, and episode coordinates. The manifest records model and
adapter revisions, timestamp step, audio hashes, text hashes, placement policy,
and every transformation used to derive probabilities from returned values.

Parquet is suitable for compact tabular projections. Raw fp16 matrices remain
simple binary artifacts with explicit shape, dtype, and row indexes; do not place
large numeric arrays inside JSON.

Before inference, a dry run must report the planned endpoint-row count and exact
projected fp16 bytes. The user can then approve the full capture size or reduce the
sample without guessing.

## Aggregation Palette

Implement aggregation functions only as replayable analysis plugins over the
frozen run. The initial palette should include:

### Candidate-Level

- current core/boundary selector baseline;
- median start and end, retained only as a simple control;
- joint start/end medoid;
- densest interval cluster with abstention;
- probability-weighted medoid; and
- trimmed consensus after removing one extreme placement.

### Distribution-Level

- arithmetic probability pool on the episode-time grid;
- log-space or geometric pool with a probability floor;
- peak clustering across placements;
- mixture-mode selection using cross-placement support;
- top-k-only versions of the preceding methods; and
- coarse-histogram versions for comparison with full rows.

Every method returns a selected interval or abstention plus support count,
dispersion, dominant-mode share, and sensitivity to removing one placement. Start
and end must be handled jointly or checked for a valid ordered interval after
fusion.

Do not select the production method from automated metrics alone. Compare methods
against blind human judgments, Luna disagreement patterns, stability under repeat
inference, and behavior across series and episode position.

## Execution Plan

The source-controlled server startup, SSH tunnel, campaign, workbench, Luna, and
aggregation commands live in the companion
[`distribution-review-runbook.md`](distribution-review-runbook.md). It clearly
separates commands available on this branch from the target interface still to be
implemented.

### Phase 0: Server Handoff And Preflight

1. Confirm the checked-out commit and a clean server worktree.
2. Confirm the adapter and vLLM revisions match the campaign manifest target.
3. Confirm local loopback health without changing remote deployment state.
4. Locate the content-hashed audio cache and available disk space without printing
   credentials.
5. Run the campaign planner in dry-run mode and review projected tensor and plot
   sizes.
6. Bind the research workbench to loopback and establish the user's SSH port
   forward.

### Phase 1: One-Episode Capture Spike

1. Add an opt-in adapter path that returns or writes selected raw fp16 timestamp
   rows while preserving the current compact endpoint.
2. Capture five placements for the existing BECK episode targets and controls.
3. Prove that compact argmax/entropy values recomputed from saved tensors match the
   current adapter output.
4. Prove row-to-token and crop-to-episode projection identity.
5. Render full, top-k-64, and coarse-histogram plots from the same rows.
6. Measure artifact size, write speed, and plot latency.

### Phase 2: Representative Review Workbench

1. Generate the approximately 120-cue input-pinned sample.
2. Capture five placements and all three distribution representations.
3. Implement campaign overview, sample queue, cue detail, and append-only human
   judgments.
4. Add on-demand registered clips and bounded caches.
5. Conduct a first human pass without Luna output visible.
6. Run the three-response Luna fanout and compare findings afterward.
7. Decide whether full rows add useful distinctions beyond top-k 64 plus the
   coarse histogram.

### Phase 3: Aggregation Replay

1. Implement the candidate-level baselines.
2. Add distribution pools only after plot review supplies concrete shape cases.
3. Show all method outputs in the aggregation playground.
4. Select a blind comparison cohort covering agreements, disagreements, and
   abstentions.
5. Report human acceptance, failure category, coverage, repeat stability, and
   cross-series behavior for each method.

### Phase 4: Full Slice

1. Freeze the chosen capture representation and placement policy.
2. Run the five-placement campaign across the full cleaned slice.
3. Materialize compact cohort tables and only the plots requested during review.
4. Apply the retained aggregation palette without repeating inference.
5. Produce a revision-bound recommendation for an aggregation rule or conclude
   that the model remains unsuitable for automatic retiming.

### Phase 5: Audio Preprocessing Ablations

After the raw-audio aggregation behavior is understood, replay the same pinned
sample and review process for the separately defined channel, separation, or
segmentation arms. Those runs remain independently attributable and reuse the same
web workbench and judgment schema.

## Stop Conditions

Pause before the full-slice run if any of the following occurs:

- saved tensors cannot reproduce the adapter's compact results;
- row identity cannot be joined unambiguously to token endpoints;
- projected storage is unexpectedly large and no approved bounded representation
  exists;
- full and compact plots use inconsistent probability transformations;
- clip decoding changes source-clock duration or introduces an unmeasured offset;
- the sample is dominated by one series or one failure shape; or
- human labels are not concrete enough to distinguish border quality from absent
  or incorrectly cleaned text.

## Decision Outputs

The experiment should end with explicit answers to four decisions:

1. **Capture:** full rows, top-k 64 plus tail/coarse shape, or compact summary only.
2. **Review:** which plot and sampling views materially help human inspection.
3. **Aggregation:** which methods improve accepted borders at useful coverage and
   when they must abstain.
4. **Promotion:** whether the research workbench remains temporary or whether a
   stable review product and service boundary are now justified.
