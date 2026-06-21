# Subsync Reader/TUI Parity

## Goal

Give the browser reader the TUI's subtitle-management features without copying subtitle-processing code into JavaScript.

Feature parity means both surfaces can:
- Infer an episode number from the media filename;
- Load multiple local subtitle candidates;
- Fetch candidates from Kitsunekko by AniList or TVDB ID;
- Fall back to browsing the series files when an episode lookup fails;
- Read SRT and ASS candidates;
- Promote the selected candidate to `{media_stem}.srt`.

## Architectural Split

To avoid "Core Bloat," we distinguish between **Domain Primitives** (stateless, pure logic) and **Application Services** (orchestration and side-effects).

### 1. Domain Primitives (`packages/core`)
*Stateless utilities and shared contracts. These are "pure" and have no knowledge of the specific Subsync feature flow.*

**`ja_media_core.transcripts`**
- `read_subtitle(path)`: The entry point for parsing SRT or ASS into cues.
- `parse_ass()`: Pure ASS $\rightarrow$ Cues parser.

**`ja_media_core.subsync` (The Abstraction)**
- `SubtitleCandidate`: Data class representing a subtitle track (path, cues, IDs).
- `is_supported_subtitle_file(file)`: Pure check for `.srt` or `.ass` extensions.
- `infer_episode_number(path)`: Pure utility to extract episode integers from filenames.

### 2. Application Services (`packages/frontend/src/ja_media_frontend/subsync/`)
*Orchestration logic that composes primitives to implement the Subsync feature. This layer handles the filesystem and network.*

**Remote Materialization**
- `materialize_remote_track(client, file, download_dir)`:
  - Composes `KitsunekkoClient` $\rightarrow$ Disk Write $\rightarrow$ `read_subtitle` $\rightarrow$ `SubtitleCandidate`.

**Candidate Promotion**
- `promote_subtitle(candidate, media_path, overwrite=False)`:
  - Composes `SubtitleCandidate` $\rightarrow$ SRT Formatting $\rightarrow$ Atomic Disk Write.

**Input Resolution**
- `resolve_subtitle_inputs(inputs)`:
  - Composes glob expansion and validation into a list of `Path` objects.

### 3. Reusable Frontend Infrastructure

**`ja_media_frontend.audio`**
- `materialize_audio(source)`: Performs one sequential ffmpeg decode of the
  first audio stream into mono 48 kHz `int16` PCM. This avoids repeated
  container seeks and network reads when an MKV lives on NFS-backed HDDs.
- `MaterializedAudioPlayer`: Plays zero-copy frame slices with
  `sounddevice.play()` and interrupts them with `sounddevice.stop()`.

The audio module deliberately knows nothing about subtitles or Textual. The
subsync TUI owns only cue selection and the small amount of status polling
needed to remove its playback indicator.

**`ja_media_frontend.subsync.dialogs`**
- Owns the four Textual modal screens and their input validation.
- Depends on small request/state records from `subsync.models`, not on the app.

**`ja_media_frontend.widgets.timeline`**
- Renders any sequence of objects with `start_s` and `end_s` attributes.
- Owns span overlap, active-span highlighting, tick labels, and responsive
  width calculation without depending on subtitle or subsync application state.

---

## Implementation Order

### 1. Domain Primitives (The Foundation)
- Move ASS parsing to `transcripts.py`.
- Define `SubtitleCandidate` and `is_supported_subtitle_file` in `core.subsync`.
- Move `infer_episode_number` to `core.subsync`.

### 2. Application Services (The Glue)
- Create the `ja_media_frontend.subsync` package.
- Implement `materialize_remote_track` by composing the client and the core parser.
- Implement `promote_subtitle` using atomic write logic.
- Move `resolve_subtitle_inputs` here.

### 3. Frontend Integration
- **TUI**: Replace local methods with calls to `subsync.service`, `subsync.utils`, and core.
- **Reader**: Add FastAPI routes that call the shared package to fetch and promote subtitles.
- **JS**: Implement the candidate list, fetch button, and promote button.

## Resulting File Structure

```text
packages/core/src/ja_media_core/
├── transcripts.py       # ASS/SRT parsing and formatting (Pure)
└── subsync.py           # SubtitleCandidate model, extension check, ep-inference (Pure)

packages/frontend/src/ja_media_frontend/
├── audio.py             # Full-source PCM materialization and range playback
├── widgets/
│   └── timeline.py      # Reusable Textual timed-span visualization
├── subsync/
│   ├── dialogs.py       # Focused Textual modal screens
│   ├── models.py        # Dialog and remote-lookup state contracts
│   ├── service.py       # Candidate loading, retrieval, materialization, promotion
│   ├── utils.py         # Discovery, input resolution, remote-row search
│   ├── tui.py           # Textual entry point
│   └── reader.py        # Browser entry point
├── subsync_tui.py       # Legacy import compatibility
└── subsync_reader.py    # Legacy import compatibility
```
