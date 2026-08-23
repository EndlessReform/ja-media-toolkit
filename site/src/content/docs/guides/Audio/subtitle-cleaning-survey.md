---
title: Subtitle cleaning survey
description: Run bounded subtitle-cleaning corpus slices against local or hosted models.
---

The subtitle cleaner is a survey tool for finding recurring cleanup patterns. Its
model output is not part of the production subtitle path.

## Generate requests

Run from `packages/frontend`:

```sh
uv run ja-media-srt-clean generate \
  --anilist 184591 \
  --run-hash \
  --model gpt-5.6-luna
```

Generation downloads the matching Kitsunekko SRTs and writes OpenAI-compatible
request JSONL plus a local manifest. Use a preserved `--run-hash` directory when
comparing models. A corpus slice should stay bounded: a small set of series, one
episode per series, and all distinct releases for that episode.

To rerun a recipe against the exact cached inputs from an earlier corpus slice,
use its window manifest instead of downloading the current Kitsunekko files:

```sh
uv run ja-media-srt-clean generate \
  --source-manifest ../../output/srt-clean/corpus-slice-2026-08-21/clean.manifest.jsonl \
  --out ../../output/srt-clean/corpus-slice-2026-08-22/clean \
  --model openai/gpt-5.6-luna-20260709 \
  --single-jsonl
```

Generation verifies every cached source hash. The model-visible text receives
the approved deterministic cleanup first, and the manifest records the rules
and review flags for each cue. Add `--flagged-windows-only` to generate only
ten-cue windows containing at least one flagged cue; this is an optional
cost-saving route, not the recipe used for the full second pass.

## Run GPT-5.6 Luna

Put `OPENAI_API_KEY` in the repository `.env`, then run a small trial before the
whole file:

```sh
uv run ja-media-srt-clean run-provider \
  --anilist 184591 \
  --run-id sha256-REPLACE_ME \
  --provider openai \
  --model gpt-5.6-luna \
  --limit 20 \
  --out ../../output/srt-clean/gpt-5.6-luna.jsonl
```

The OpenAI preset uses `https://api.openai.com/v1`, `OPENAI_API_KEY`, and strict
JSON-schema output. GPT-5.6 Luna supports Chat Completions and structured output;
see the [official model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

## Run DeepSeek V4 Flash

Put `DEEPSEEK_API_KEY` in the repository `.env`:

```sh
uv run ja-media-srt-clean run-provider \
  --anilist 184591 \
  --run-id sha256-REPLACE_ME \
  --provider deepseek \
  --model deepseek-v4-flash \
  --body-json '{"thinking":{"type":"disabled"}}' \
  --limit 20 \
  --out ../../output/srt-clean/deepseek-v4-flash.jsonl
```

The DeepSeek preset uses `https://api.deepseek.com`, `DEEPSEEK_API_KEY`, and JSON
object mode. DeepSeek documents `deepseek-v4-flash` as the current model name and
supports OpenAI Chat Completions; see its [official change log](https://api-docs.deepseek.com/updates/).

Remove `--limit` after inspecting the trial results. Pass `--force` only when an
existing result file is intentionally being replaced.

## Use another provider

Any OpenAI-compatible Chat Completions endpoint can be selected explicitly:

```sh
uv run ja-media-srt-clean run-provider \
  --anilist 184591 \
  --provider custom \
  --base-url http://localhost:8000/v1 \
  --model my-model \
  --api-key-env MY_MODEL_API_KEY \
  --response-format json-object
```

`--response-format` accepts `keep`, `json-object`, or `none`. Use `--body-json`
for small provider-specific request fields without changing generated artifacts.

## Run the corpus slice through OpenRouter

The prepared slice contains 1,012 requests across 12 series, 12 episodes, and 26
distinct SRT files. Put `OPENROUTER_API_KEY` in the repository `.env`, then run
from `packages/frontend`:

```sh
caffeinate -i uv run ja-media-srt-clean run-provider \
  --input ../../output/srt-clean/corpus-slice-2026-08-21/clean.batch-00001.jsonl \
  --manifest ../../output/srt-clean/corpus-slice-2026-08-21/clean.manifest.jsonl \
  --out ../../output/srt-clean/corpus-slice-2026-08-21/luna-openrouter.results.jsonl \
  --provider custom \
  --base-url https://openrouter.ai/api/v1 \
  --api-key-env OPENROUTER_API_KEY \
  --model openai/gpt-5.6-luna-20260709 \
  --concurrency 12
```

The output is flushed after every completed request. If the command is stopped,
run the same command again with `--resume`; IDs already present in the output are
skipped. Progress is printed every 25 requests. `caffeinate -i` keeps macOS from
sleeping while the command runs.

After completion, the result file should contain 1,012 rows:

```sh
wc -l ../../output/srt-clean/corpus-slice-2026-08-21/luna-openrouter.results.jsonl
```

The sibling `luna-openrouter.results.execution.json` records the requested and
returned model names. A hosted-provider run reconstructs its results
automatically. The standalone `reconstruct` operation remains available for
imported or asynchronous results. Invalid windows are written to the
reconstruction directory's `dlq.jsonl`.

For the deterministically pre-cleaned second pass, run:

```sh
caffeinate -i uv run --env-file ../../.env ja-media-srt-clean run-provider \
  --input ../../output/srt-clean/corpus-slice-2026-08-22/clean.batch-00001.jsonl \
  --manifest ../../output/srt-clean/corpus-slice-2026-08-22/clean.manifest.jsonl \
  --out ../../output/srt-clean/corpus-slice-2026-08-22/luna-openrouter.results.jsonl \
  --provider custom \
  --base-url https://openrouter.ai/api/v1 \
  --api-key-env OPENROUTER_API_KEY \
  --model openai/gpt-5.6-luna-20260709 \
  --concurrency 12
```

The resulting review directory is
`luna-openrouter.reconstruct/`. If interrupted, rerun the same command with
`--resume`.

## Repair and failed windows

Each provider call is independent. If the returned JSON fails validation, the
runner makes one repair call containing the same cues, the invalid response, and
the validation messages. If that also fails, the result row records
`validation_retry_exhausted` and both attempts. Set `--repair-attempts 0` to
disable repair.

Transport retries are separate. HTTP 429, retryable server responses, and
network errors use Tenacity with jittered exponential backoff. A provider
`Retry-After` header takes precedence. The default is eight total request
attempts; override it with `--request-attempts`.

Reconstruction writes failed rows to `reconstruct/dlq.jsonl` and does not publish
an incomplete source as a complete cleaned SRT.

## Review the completed Luna corpus

The completed run has already been reconstructed. Open all 12 series from
`packages/frontend` with:

```sh
uv run ja-media-srt-clean review \
  --run-dir ../../output/srt-clean/corpus-slice-2026-08-21/luna-openrouter.reconstruct
```

Use `[` and `]` to move through series/episode pairs, `j` and `k` to switch
subtitle sources within the selected pair, and `n` or `N` to jump forward or
backward between decisions that were not accepted as-is. Press `s` for edit and
remove reason counts on both the selected subtitle track and the whole run.

Press `F5` for cue review or `F6` for the whole-run reason pivot. The pivot
shows each reason's cue count, share of all changed cues, edit/remove split, and
coverage across series, episodes, and subtitle sources. Use the arrow keys or
`j` and `k` to select a reason. Use `n`/`N` or Page Down/Page Up to browse the
matching cues. Multi-reason cues appear in every applicable row, so percentages
can sum to more than 100%.

Within `F5`, press `r` to overlay the experimental deterministic cleanup rules
on the existing cue rail. The rail then shows exact matches, partial matches,
untouched cues, changes to model-accepted cues, other disagreements, and
unscored cues. The detail pane compares the normalized input, deterministic
candidate, and saved model target. Press `R` for whole-run scores by rule and
saved cleanup reason and suspicious-cue flag counts. While the overlay is active,
`n` and `N` jump between
partial matches, disagreements, changes to accepted cues, and unscored cues.
Press `f` or `F` to jump specifically to the next or previous flagged cue.
For the first Luna run this remains a read-only projection. Requests regenerated
with the current recipe apply these rules before the model and record their
names in `active_rules`.
Reason summaries omit cues already reduced to empty text by deterministic
cleanup; their counts belong to the rule summary rather than the model-reason
pivot.

The active candidate profile covers reviewed width and middle-dot normalization,
obvious `次回`/`つづく` paratext, explicit translated-language captions,
parenthetical and bracketed labels, reading glosses, line-leading `≪` speaker
markers, and terminal U+2015 `―`. The terminal-bar rule was promoted after all
339 scored occurrences in this corpus slice removed or replaced it; the four
remaining occurrences were unscored escalations. It does not touch katakana
U+30FC `ー` or U+2014 `—`.

Angle-wrapped text, internal `≪（speaker）` boundaries, whole-cue curly or ASCII
quotes, repetitive kana, and very short kana are flags rather than edits. A
flagged cue remains unchanged so a later filtered model call retains the marker
that triggered review. `R` reports both cue yield and the saved model windows a
window-level filtered run would touch.

Repetition is only a routing signal, not a deletion rule. A 100-cue review using
the learner/ASR transcript target found 50 whole-cue removals, 29 mixed cues that
needed non-lexical material edited out, 20 cues worth keeping, and one ambiguous
cue. The filtered model must distinguish meaningful words and discourse items
from written laughter, breaths, groans, screams, exertion, lyrics, and tactile
SFX. Predictable non-lexical material is removed before forced alignment rather
than shifted into its low-confidence queue.

## Compare requested and served models

Provider and local-vLLM runs write a sibling `*.execution.json` file containing:

- the selected provider;
- the requested model name; and
- model names returned in successful responses.

This catches aliases and local servers that accept one request name while serving
another model.
