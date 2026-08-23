# Cleaned Subtitle Forced Alignment: First Episode

Status: working first slice; implementation and full BECK run are complete. Human
listening review is the remaining gate before choosing a follow-up episode.

## Outcome

Take the completed cleaned subtitle for BECK episode 1, align every retained cue
against its canonical Japanese audio, write a retimed SRT, and make failures easy
to hear and inspect in the existing cleaning review UI.

This slice answers four immediate questions:

1. Can deterministic or final cleaned cues drive the aligner without using SRT as
   the join format? Yes. Stable JSONL records carry both text bases and source cue
   identity.
2. Can Qwen results be reconstructed into episode-clock cues? Yes. The client maps
   word buckets to cue IDs, adds the crop start, and writes 329 retimed cues.
3. Which unpadded window works for this episode? Use 60 seconds. The 180-second arm
   produced severe timestamp failures in two of three comparison regions.
4. Does Qwen expose a direct probability that text exists in the audio? No. Its
   endpoint distributions and invalid timestamp geometry provide review signals,
   but this first control set does not justify automatic rejection.

## Data Flow And Ownership

```text
cleaning manifest + decisions + reconstructed cleaned SRT
  -> client joins 410 source cues by source hash and original cue index
  -> client writes 329 retained cue records + 81 exclusions
  -> client resolves the pinned canonical episode row
  -> client reads and hashes the exact Bronze audio object
  -> client cuts consecutive, unpadded mono 16 kHz windows
  -> vLLM/Qwen returns a probability vector for each requested token boundary
  -> client selects timestamp buckets and records probability/margin/entropy
  -> client joins tokens to stable cue IDs and adds each window's episode offset
  -> client writes episode-clock JSON + retimed SRT
  -> cleaning review joins candidates back to the 410-cue source and plays audio
```

vLLM owns model execution and token-classification probabilities. It does not know
about SRT cues, source hashes, Bronze, episode offsets, cleaning decisions, or the
review UI. All cue reconstruction and artifact writing are client-side.

SRT is an input/output convenience, not the transfer contract between cleaning and
alignment. `input-cues.jsonl` is that contract. It retains `source_index`, stable
`cue_id`, source borders, `mechanical_text`, final `alignment_text`, cleaning action,
and cleaned SRT ordinal. The runner accepts `--text-base mechanical|cleaned`.

## Pinned Case

```text
series: BECK
AniList: 57
episode: 1
subtitle ID: f2c5d0a0-4cb8-563b-b4de-5b7efd131465
source SHA-256: f9769b7926c8cf41607064df5c373d8cd4c0457248c5a54bfc8bb7d611673867
source cues: 410
retained alignment cues: 329
excluded cues: 81
audio duration: 1459.936 seconds
```

Canonical provenance lives in `case.json`, including Silver materialization and
snapshot IDs, the canonical input fingerprint, Bronze object locator, local audio
SHA-256, codec, stream, duration, and byte count.

## Artifacts

All local run artifacts live under:

```text
research/forced-alignment-retiming/output/beck-01-netflix-cleaned/
```

Important files:

```text
case.json
inputs/input-cues.jsonl
inputs/excluded-cues.jsonl
inputs/cleaned.srt
audio/bd048df5037de583286b1ffa8f7a8a9dfb44c1ce3aadb28bd2a487b4218b1190.ac3
window-comparison/results.json
window-comparison/results-180.json
full-alignment/results.json
full-alignment/retimed.srt
confidence-controls/results.json
summary.md
```

## Completed Checklist

- [x] Join the supplied cleaning run to the original BECK source by subtitle ID,
  source hash, and original cue index.
- [x] Prove that retained JSONL records reproduce the reconstructed cleaned SRT.
- [x] Retain both deterministic and final cleaned text on every input cue.
- [x] Resolve the pinned canonical Silver row and read the exact audio from Bronze.
- [x] Cache and hash the Japanese AC3 locally without writing to DEV or Bronze.
- [x] Cut exact, unpadded 30-, 60-, and 180-second mono 16 kHz crops.
- [x] Run the same early, middle, and late cue identities through all three arms.
- [x] Run every retained cue across the episode at the selected 60-second size.
- [x] Convert local Qwen buckets to episode time and reconstruct cue envelopes.
- [x] Write a 329-cue retimed SRT from final cleaned text.
- [x] Join candidates to the original 410-cue source in the cleaning review UI.
- [x] Decode the canonical AC3 through the review audio loader.
- [x] Play retimed rather than source borders when an alignment candidate exists.
- [x] Show score components and alignment status in the cue detail panel.
- [x] Make next/previous flagged navigation include suspicious alignments.
- [x] Run present, shifted-audio, unrelated-text, inserted-text, and nonspoken-label
  controls.
- [ ] Listen through the worst ordinary-dialogue cues and label whether each problem
  is absent text, cleanup error, wrong border, or model timestamp failure.

## Window Findings

The comparison targets are around 280, 730, and 1,169 seconds. None is in the
opening or ending music-only region.

- The early cue agrees within 40 ms between 30 and 60 seconds. The 180-second arm
  stretches the same cue to 70.08 seconds and reverses token order.
- The middle cue is plausible in all arms, but 60 seconds has cleaner endpoint
  distributions than 180 seconds. The 30-second arm contains a reversed endpoint.
- The late cue fails in every arm. The 30- and 60-second spans are 17.12 and 15.28
  seconds; the 180-second span is 79.28 seconds and touches the crop edge.

The full 60-second run covers all 329 retained cues in 22 populated windows. Empty
music/SFX windows are skipped because there is no spoken text to align. Failures
occur throughout ordinary dialogue, not only songs or credits:

```text
aligned: 174
suspicious: 155
contains reversed token: 124
contains backward token order: 113
touches crop edge: 80
aligned span over 15 seconds: 66
```

For contrast, the 540-600 second window aligned 18/18 cues and the 1320-1380
second window aligned 7/7. The ordinary-dialogue windows from 960-1080 seconds
flagged 24/32 cues.

## Absent-Text Findings

The present control is aligned with no reversed or backward tokens. All four wrong
arms are suspicious and contain at least one reversed token. Maximum normalized
entropy is 0.492 for the present control and 0.615-0.760 for the wrong arms.

Those observations do not produce a calibrated absent-text probability:

- reversed-token review would queue 124/329 episode cues;
- entropy >= 0.60 would queue 144/329 cues;
- any structural suspicious status would queue 155/329 cues; and
- minimum endpoint probability below 0.04 would queue 135/329 cues and misses the
  nonspoken-label control.

Use structural status first and the probability fields to order human review. Do
not automatically discard text from this first five-arm control set.

## Immediate Review Command

```sh
cd packages/frontend
uv run ja-media-srt-clean review \
  --run-dir ../../output/srt-clean/corpus-slice-2026-08-22/luna-openrouter.reconstruct \
  --alignment-case ../../research/forced-alignment-retiming/output/beck-01-netflix-cleaned/case.json \
  --episode 1
```

The case supplies canonical audio automatically. `f` and `F` move among cleaning
flags and suspicious alignments; cue playback uses the retimed borders.

## Next Decision

Listen to a small worst-first sample across several ordinary-dialogue windows. If
the bad geometry corresponds to genuinely absent or badly cleaned text, keep the
current ranking signals and move to a second episode. If much of the text is
present but Qwen still reverses or stretches timestamps, change the prompt/text
unit policy before scaling beyond this episode.
