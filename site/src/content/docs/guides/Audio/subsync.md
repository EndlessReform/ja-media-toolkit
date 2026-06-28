---
title: Subsync
description: Guide to reviewing and comparing subtitles against local media using the subsync tools.
---

`subsync` provides a set of lightweight tools for reviewing and comparing subtitles against local media. Rather than performing automated model runtime work, these tools are designed to help you verify the timing and accuracy of existing subtitle files.

## TUI (Terminal User Interface)

The TUI version allows you to load one media file and multiple `.srt` candidates simultaneously. It displays subtitle activity on a text-based timeline and lets you listen to exact cue boundaries to compare different versions. You can also fetch subtitle candidates from the Kitsunekko service using an AniList or TVDB ID.

### How to run it

If you have installed the `ja-media` tool globally, simply run:

```sh
ja-media subsync tui path/to/media.mp3 'path/to/subs/*.srt'
```

For local development within the repository:

```sh
cd packages/frontend
uv run ja-media subsync tui ../../examples/input/example_走る高級レストランに乗ってきた.mp3 '../../subs/*.srt'
```

You can also pass a single `.srt` path instead of a glob:

```sh
uv run ja-media subsync tui ../../media/episode.mkv ../../subs/episode.ja.srt
```

*Note: Please quote glob patterns so the CLI can handle expansion, sorting, and validation.*

To fetch candidates from the Kitsunekko service without local files:

```sh
uv run ja-media subsync tui ../../media/GANTZ.S01E16.mkv \
  --anilist 395 \
  --fetch-subs \
  --sort-by-language
```

For media produced by `audio-library ingest`, the series ID is inferred from
the nearest `anilist-<id>` parent directory. For example, this needs no
`--anilist` argument:

```sh
uv run ja-media subsync tui \
  ../../media/derived-audio/anilist-101573/S01E001.m4a \
  --fetch-subs
```

An explicit `--anilist` or `--tvdb` value always takes precedence over path
inference.

Every candidate is assigned a compact language label in the `LID` column:
`ja`, `?`, `bi`, `non-ja`, or `short`. Candidate order remains unchanged by
default. Pass `--sort-by-language` to place Japanese candidates first, followed
by unknown, bilingual, non-Japanese, and insufficient-text candidates. The
thresholds come from the global `[subtitles.language_id]` configuration.

By default, the TUI uses the original audio for playback and the VAD speech
track. Pass `--vocal-separation` to run Demucs and produce a cleaner `vocals`
stem before opening the TUI, so background music is less likely to mask
cue-boundary checks. This adds seconds-to-minutes of startup time depending on
episode length and accelerator (MPS on Apple Silicon). Subtitle promotion and
sidecar paths still target the original media file.

```sh
uv run ja-media subsync tui episode.mkv --vocal-separation --anilist 183385
```

Demucs is installed by the Apple runtime environment. It defaults to the
`htdemucs` model and auto-selects MPS on Apple Silicon. To verify the real
backend instead of only mocked adapter behavior, run an opt-in smoke test
against a short audio fixture:

```sh
cd envs/apple
JA_MEDIA_RUN_DEMUCS_SMOKE=1 \
JA_MEDIA_DEMUCS_SMOKE_AUDIO=/path/to/short.wav \
uv run pytest tests/test_vocal_separation.py -k real_demucs_smoke
```

The TUI attempts to parse the episode number from the filename using `parse-torrent-title`. If the filename is ambiguous, you can override it:

```sh
uv run ja-media subsync tui ../../media/gantz.mkv \
  --tvdb 79099 \
  --episode 16 \
  --fetch-subs
```

While the TUI is running, you can press `F6` to open a modal and change the AniList/TVDB ID or episode number to fetch a new set of candidates.

### Understanding the Layout

The TUI is divided into four main regions:
- **Source Media Path**: The file currently being reviewed.
- **Candidate Table**: A list of `.srt` files showing language classification, cue counts, the current cue, active subtitle time, and total span.
- **Activity Timeline**: A visual representation of subtitle spans for the selected candidate.
- **Cue Text**: The text of the currently selected cue.

The timeline uses colored half-blocks to represent subtitle spans and blank space for gaps. Highlighting the selected cue makes it easier to inspect timing edges during playback.

### TUI Key Bindings

| Key | Action |
| --- | --- |
| `space` | Play or stop the current cue |
| `h` / `l` | Previous / next cue in the selected `.srt` |
| `j` / `k` | Previous / next `.srt` candidate |
| `gg` / `G` | Jump to start / end |
| `Ctrl-f` / `Ctrl-b` | Page the timeline forward / backward |
| `Ctrl-d` / `Ctrl-u` | Half-page the timeline forward / backward |
| `+` or `=` | Zoom in (show fewer seconds) |
| `-` / `_` | Zoom out (show more seconds) |
| `F6` | Configure IDs/episode and fetch Kitsunekko candidates |
| `q` | Quit |

## Browser Reader

The `subsync reader` launches a local browser-based interface that pairs one media file with one SRT file. It serves a temporary FastAPI application on `127.0.0.1` and renders subtitle text as standard DOM text, making it compatible with browser-based dictionary extensions. It uses the browser's audio seeking capabilities to play the specific cue boundaries from your media file.

### How to run it

If you have installed the `ja-media` tool globally, simply run:

```sh
ja-media subsync reader path/to/media.mp3
```

For local development within the repository:

```sh
cd packages/frontend
uv run ja-media subsync reader ../../examples/input/example_走る高級レストランに乗ってきた.mp3
```

By default, the reader searches for a matching `.srt` sidecar file based on the media filename. It first checks for `episode.srt`, and then looks for any sibling `episode*.srt` match. If there are multiple potential matches, you can specify the file explicitly:

```sh
uv run ja-media subsync reader episode.m4a --sub-file episode.ja.srt
```

The reader will open your default browser automatically. If you are running this in a remote shell or performing smoke tests, use the `--no-open` flag.

### Reader Key Bindings

| Key | Action |
| --- | --- |
| `space` | Play or stop the current cue |
| `j` / `l` | Next cue |
| `k` / `h` | Previous cue |
| `gg` / `G` | Jump to start / end |
| `f` / `b` | Page the timeline forward / backward |
| `d` / `u` | Half-page the timeline forward / backward |
| `+` or `=` | Zoom in (show fewer seconds) |
| `-` or `_` | Zoom out (show more seconds) |

The interface features two timeline lanes: a media duration baseline and a subtitle cue activity track. Japanese text is left-aligned, selectable, and can be switched between Gothic, Mincho, and system font stacks.

---

## Developer Notes

### Technical Note on Playback

To ensure high performance and exact timing, the TUI decodes the first audio stream using `ffmpeg` into mono 48 kHz signed 16-bit PCM, which is held in RAM. It then plays cue-sized byte slices through `sounddevice`. This approach avoids repeated container seeks and ensures that cue boundaries map to exact sample offsets. Moving between cues (`h` or `l`) immediately aborts the active output stream before starting the next one.

### Known Limitations & Roadmap

The current version of `subsync` focuses on providing a manual review surface. The following features are planned for future releases:
- **Automatic Scoring**: Scoring candidates based on VAD (Voice Activity Detection).
- **VAD Comparison**: Visualizing subtitles against voice activity.
- **Timing Adjustments**: Ability to nudge offsets and write them back to sidecar files.

**Requirements:**
- `ffmpeg` must be available on your `PATH`.
- `sounddevice` must be installed in the active environment.
- **Memory**: Startup decodes the audio into memory. For example, a typical 24-minute episode takes roughly 138 MB of RAM in the mono 48 kHz format.
