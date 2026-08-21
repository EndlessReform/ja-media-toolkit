# Concrete naming and forbidden umbrella terminology

Status: proposed cleanup design; enforcement approved.

## Recommendation

Remove `evidence` from the repository's working vocabulary. Replace it with a
name that describes what the value, operation, or interface actually does in
its local domain.

The replacement process is context first, vocabulary second. Do not begin by
classifying a value into a repository-wide taxonomy. Read its producer,
consumers, grain, and purpose; then give it the narrowest useful name. The word
list below is fallback guidance for cases where the concrete domain name is not
already obvious.

This cleanup must not introduce a universal provenance model, metadata
envelope, type hierarchy, or new schema merely to make terminology uniform.

## Problem

The forbidden word currently stands for unrelated things: parser output,
scores, source metadata, decision explanations, hashes, logs, files, reviewer
inputs, acceptance rules, and support for prose claims. It conceals what a
field contains and encourages generic JSON bags and UI panels.

Replacing it mechanically with another umbrella word would preserve the same
problem. The goal is truthful local names, not a new controlled ontology.

## Naming method

For each occurrence:

1. Inspect the value itself and the code that produces and consumes it.
2. Ask what a maintainer needs to know at that location—not everything known
   about the value across the system.
3. Prefer the existing domain noun: `episode_tokens`, `language_score`,
   `candidate`, `override_reason`, `input_fingerprint`, or `aligned_srt`.
4. Include a unit, scope, or operation when it prevents ambiguity:
   `cue_shift_ms`, `japanese_script_ratio`, `selected_candidate`, or
   `resolution_diagnostics`.
5. Preserve source/version information only when a real consumer needs it for
   reproduction, invalidation, conflict resolution, or display.
6. Use the fallback vocabulary only when a more concrete name would be
   misleading or needlessly long.

Do not add fields such as `origin`, `status`, `kind`, or `provenance` by default.
Do not force every source into a stable classification. The repository has many
changing inputs, and their ownership is usually clearer from their domain
contracts and surrounding code.

## Worked examples

### Episode resolution

If a JSON object contains manifest hints, parsed episode tokens, title matches,
and rejection messages, call it `resolution_context` only when consumers need
the aggregate. Inside it, retain concrete field names such as
`manifest_series_hint`, `parsed_episode_numbers`, `title_match`, and
`diagnostics`.

Renames for the existing aggregate can remain shape-preserving:

```text
EpisodeEvidence                   -> EpisodeResolutionContext
collect_episode_evidence          -> build_resolution_context
HintClaim.evidence                -> resolution_context
BindingProposal.proposal_evidence -> resolution_context
EpisodeResolutionPlan.evidence    -> resolution_context
ResolutionResult.evidence         -> resolution_context
```

`context` is justified here because the object is genuinely a mixed input to a
resolution decision. It is not the preferred replacement everywhere. Do not
split this object into abstract source/observation/signal categories unless a
specific consumer or invariant makes that structure useful.

### Subtitle matching

Use names that describe each stage:

- `kitsunekko_candidates` for retrieved candidate records;
- `language_score` for one classifier output;
- `activity_overlap` for the measured overlap;
- `alignment_result` for the transform output;
- `aligned_srt` for the produced file; and
- `review_notes` for a human annotation.

These values do not need a shared semantic parent. Their relationship is
already expressed by the matching workflow.

### Canonicalization and overrides

Prefer `source_record`, `candidate`, `selection_reason`, `override`,
`input_fingerprint`, and `materialization_id` where those are the actual
contents. A binding does not need a generic metadata wrapper just because some
of those fields help explain or invalidate it.

### Operator UI

Name panels after what users can inspect or do: `Candidates`, `Match scores`,
`Diagnostics`, `Artifacts`, `Run logs`, and `Override history`. Do not create a
single generic panel for heterogeneous details, and do not change persisted
schemas merely to make UI headings uniform.

### Architectural prose

State the result that supports the conclusion:

```text
Before: The spike provides strong evidence for transactional catalog behavior.
After:  The two-process test committed atomically without lost updates.
```

If a strict logical demonstration is intended, use `proof` and state the
premises and conclusion.

## Fallback vocabulary

This is a small word bank, not a taxonomy that values must join.

- **Input or source record** — data consumed by the operation. Prefer the
  actual domain noun, such as `manifest`, `subtitle_candidate`, or
  `series_metadata`.
- **Metric or score** — a numeric result. Name the quantity and unit whenever
  possible, such as `boundary_error_ms` or `title_similarity`.
- **Result** — the direct output of a computation when no narrower domain noun
  exists. Prefer `language_classification` or `alignment_spans` when available.
- **Diagnostic** — an explanation of a failure, warning, rejected invariant,
  or suspicious state. Do not use it as a container for successful output.
- **Artifact** — a durable produced file or dataset, such as an aligned SRT,
  plot, log, or comparison table.
- **Candidate** — one option under consideration before selection.
- **Reason** — a concise explanation attached to a decision or override.
- **Criterion** — a rule used to accept, publish, quarantine, or escalate.
- **Finding** — a conclusion in a report based on named measurements, test
  results, or review. State those inputs nearby.
- **Fingerprint, revision, hash, run ID, or materialization ID** — use the
  exact identifier instead of a generic lineage label.

Terms such as `fact`, `observation`, `signal`, and `provenance` are deliberately
not top-level categories here. They can be correct English in a specific local
contract, but their boundaries are unstable and they should not drive a
repository-wide data model.

## Scope discipline

This terminology pass authorizes renaming, not architectural expansion.

- Preserve existing data shapes unless a consumer already needs a change.
- Do not introduce generic metadata bags or enum registries.
- Do not add source classification solely for completeness.
- Do not duplicate information that is clear from the owning table, type,
  endpoint, or workflow.
- Do retain hashes, revisions, model versions, and input identities where they
  already enforce reproducibility or stale-decision checks.
- Treat each broad replacement such as `context`, `data`, `details`, or `proof`
  as suspect if it obscures the contents again.

## Enforcement boundary

The tracked `.githooks/pre-commit` hook scans the complete staged tree rather
than only added lines. It checks filenames and case-insensitive file content in
source code, tests, scripts, templates, configuration, and SQL.

Activate it once in each clone:

```sh
git config core.hooksPath .githooks
```

The hook intentionally fails while unaudited source occurrences remain. Four
checksum-protected DuckLake migrations are retained as immutable history:

- `001_identity.sql`;
- `002_identity_views.sql`; and
- `003_phase_d_subtitle_lid.sql`; and
- `009_resolution_context.sql`.

The fourth migration must name the two legacy columns once in order to rename
them. New migrations are otherwise checked normally. `AGENTS.md` and this
design document may quote the forbidden token solely to define and explain the
rule.

## Implementation plan

### 1. Guard and policy

Keep the `AGENTS.md` prohibition and staged-tree hook. They prevent new source
uses while cleanup proceeds.

### 2. Operator UI first

Inspect each affected screen and rename labels, view models, and local fields
after their actual contents or function. Do not change durable contracts unless
the UI exposes a real contract defect. Verify rendered labels and focused UI
tests.

### 3. Contracts and persisted names

Rename the episode-resolution aggregate, builder, fields, queries, fixtures,
and tests without changing resolution policy. Add a forward migration that
renames the current persisted columns; never edit applied migrations.

For every other source occurrence, review producer and consumers before
choosing a replacement. Prefer local domain terms from the naming method above.

### 4. Prose and remaining source

Rewrite docs and comments to state the concrete measurement, test result,
artifact, rule, or conclusion. Avoid mechanical substitution. Rename files and
identifiers where necessary, then update references.

### 5. Verification

Run focused tests for each changed area, then the staged-tree hook. The cleanup
is complete when only the policy/design quotations and immutable migration
exceptions remain.

## Cost and rejected alternatives

The ongoing mechanism is one small shell hook plus ordinary naming review. No
runtime framework or shared provenance contract is required.

A global search-and-replace is rejected because it would create a new vague
umbrella. A universal taxonomy is rejected because input sources and workflows
will keep changing, while local contracts already supply most context. Scanning
only newly added lines is rejected because old ambiguous identifiers could
continue crossing commit boundaries indefinitely.
