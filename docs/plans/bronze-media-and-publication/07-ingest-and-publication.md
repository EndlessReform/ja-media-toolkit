# Capture-first ingest and publication

Status: proposed workflow after orchestration selection.

## Goal

Separate capture, identity, derivation, and publication:

```text
capture -> bind -> derive/check -> bundle -> publish
```

Bronze survives later failures. Derived assets remain reusable. Audiobookshelf
receives only explicitly selected bundle versions.

## Current coupling

`audio-library ingest` currently writes AAC, hidden subtitle sidecars,
authoritative `.ja-media.json`, Audiobookshelf metadata, and cover art into the
same mounted filesystem tree. The anime-audio service indexes that tree.

Keep this layout readable as `legacy-filesystem`. New ingest stops treating an
Audiobookshelf projection as transformation history.

## Stages

### Capture

1. probe source media;
2. select audio and supported text-subtitle streams;
3. extract source audio without unnecessary transcoding;
4. store text sidecars with source/stored format provenance;
5. upload objects;
6. commit bronze manifest last;
7. notify/register the new capture partition.

Manual episode choice is not written into bronze.

### Bind

Run hints and human confirmation, then publish immutable `EpisodeBinding`.
Register the logical episode partition only after acceptance.

### Derive and check

Materialize selected assets such as `portable_aac`, LID, normalization, and
alignment. A local run may reuse already-open source bytes, but every committed
result still records the bronze/binding versions it represents.

### Bundle

Materialize a named bundle policy. Failed blocking checks prevent a new current
bundle but do not erase prior history.

### Publish

Materialize exact bundle artifacts into the configured Audiobookshelf target.
Publication may happen in the same guided user interaction, but it remains a
separate durable commit.

## CLI responsibility

`ja-media` remains the user-facing command and domain client. It does not
maintain the orchestration database or scan all S3 objects. Depending on the
selected framework, it launches a materialization/flow or performs direct
capture/human-review actions.

Compatibility commands may remain:

```text
ja-media media capture <source-dir> --anilist-id 15451
ja-media media bind --capture-id ... --episode 3
ja-media media materialize display-v1 --episode anilist-15451/e003
ja-media media publish audiobookshelf --bundle-id ...
```

`audio-library ingest --capture-to-bronze` may orchestrate these stages during
rollout. Do not silently change existing offline behavior in the first PR.

## Publication record

Store a record beside the projection, separate from Audiobookshelf metadata:

```json
{
  "schema_version": 1,
  "kind": "audiobookshelf-publication",
  "publication_id": "publication-01J...",
  "target_id": "main-anime-library",
  "bundle_id": "bundle-01J...",
  "items": [
    {"episode_key": "3", "result_id": "result-01J...", "relative_path": "S01E003.m4a", "sha256": "..."}
  ],
  "published_at": "..."
}
```

The target root is local configuration, not durable identity. Write media to a
temporary file, verify it, atomically replace it, then atomically update the
publication record and `metadata.json`.

## Failure and resume

- committed bronze is adopted after validation;
- accepted binding is immutable and reused by ID;
- deterministic derived versions are adopted if outputs verify;
- failed bundle materialization leaves the prior current pointer intact;
- failed publication leaves its prior publication record intact;
- replacing a binding, bundle, or publication always displays old and new IDs;
- prune/delete remains a separate confirmed operation.

## Acceptance

- each stage runs/resumes independently;
- normal flow commits capture before binding, binding before episode assets,
  and bundle before publication;
- the legacy filesystem library remains playable/indexable;
- publication can be rebuilt from a pinned bundle;
- control-plane/worker failure cannot corrupt the previous bundle/publication;
- CLI and docsite explain capture, binding, checks, materialization, publication,
  replacement, and recovery.
