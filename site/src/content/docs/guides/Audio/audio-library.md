---
title: Build the Anime Audio Library
---

The Phase 1 audio-library command turns one directory of anime video files
into portable M4A episodes for the Audiobookshelf podcast library. It reads
each large source once, records source provenance in a durable manifest, and
keeps every identity decision visible.

## Before you begin

You need:

- `ffmpeg` and `ffprobe` on the machine running the command;
- the AniList search service configured through `[services].root_url`;
- one source directory containing the series media files directly;
- an existing destination root, normally
  `/mnt/magi06/media/derived-audio`.

The command discovers `.mkv`, `.mp4`, `.m4v`, and `.webm` files in the
immediate source directory. It does not recurse.

## Preview an ingest

Start with a dry run:

```bash
ja-media audio-library ingest \
  --source "/srv/anime/Sousou no Frieren" \
  --destination "/mnt/magi06/media/derived-audio" \
  --dry-run
```

The wizard:

1. Searches AniList using the source directory name. If the source is a bare
   season directory such as `Season 01` or `S01`, it uses the nearest parent
   directory that is not another bare season label.
2. Requires you to select and confirm the exact AniList entry.
3. Probes every source file and automatically accepts unique PTN episode
   suggestions.
4. Prompts for missing or colliding episode mappings one at a time, with an
   `N ambiguous episodes, doing n/N` progress breadcrumb.
5. Selects an unambiguous Japanese audio stream or asks you to choose.
6. Shows the complete resolved output plan for final confirmation.

Nothing is written before the final confirmation. `--dry-run` stops after that
confirmation as well.

If you already know the series identity, skip fuzzy search:

```bash
ja-media audio-library ingest \
  --source "/srv/anime/Sousou no Frieren" \
  --destination "/mnt/magi06/media/derived-audio" \
  --anilist 154587 \
  --dry-run
```

The full metadata row is still displayed for confirmation.

## Materialize the library

Remove `--dry-run` when the plan looks correct:

```bash
ja-media audio-library ingest \
  --source "/srv/anime/Sousou no Frieren" \
  --destination "/mnt/magi06/media/derived-audio" \
  --anilist 154587
```

The initial `portable-aac-v1` profile produces AAC-LC in M4A at 128 kbps and
48 kHz, preserving mono sources as mono and limiting other sources to stereo.

Output uses the AniList ID as the stable directory identity:

```text
/mnt/magi06/media/derived-audio/
└── anilist-154587/
    ├── .ja-media.json
    ├── metadata.json
    ├── cover.jpg
    ├── S01E001.m4a
    └── S01E002.m4a
```

`.ja-media.json` is authoritative. It records normalized AniList metadata, the
selected conversion profile, source fingerprints, global ffmpeg stream
indices, and verified artifact properties. `metadata.json` and audio tags are
projections for Audiobookshelf.

After ingest, scan the **Anime Audio** podcast library in Audiobookshelf.

## Stream controls

The wizard normally prefers one audio stream tagged `jpn` or `ja`. To use the
same zero-based audio ordinal for every source:

```bash
ja-media audio-library ingest \
  --source "/srv/anime/Series" \
  --destination "/mnt/magi06/media/derived-audio" \
  --audio-stream 1
```

The displayed plan includes both the user-facing audio ordinal and ffprobe's
global stream index. ffmpeg mapping always uses the global index.

Repeat `--language` to change preference order:

```bash
--language ja --language jpn
```

## Resume and replace

The manifest is atomically checkpointed after each successful episode. Resume
an interrupted batch with:

```bash
ja-media audio-library ingest \
  --source "/srv/anime/Series" \
  --destination "/mnt/magi06/media/derived-audio" \
  --anilist 12345 \
  --resume
```

An artifact is skipped only when its source relative path, size, modification
time, selected global stream, profile, and verified output still match.

Conflicting or untracked files stop that episode instead of being adopted
silently. After inspecting the conflict, `--replace` allows the confirmed plan
to overwrite it:

```bash
ja-media audio-library ingest \
  --source "/srv/anime/Series" \
  --destination "/mnt/magi06/media/derived-audio" \
  --anilist 12345 \
  --replace
```

Failures do not remove completed episodes. Correct the problem and rerun with
`--resume`.
