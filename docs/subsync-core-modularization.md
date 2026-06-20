# Subsync Reader/TUI Parity

## Goal

Give the browser reader the TUI's useful subtitle-management features without
copying subtitle-processing code into JavaScript or pretending that Textual and
the browser need a shared UI state machine.

Feature parity here means both surfaces can:

- infer an episode number from the media filename;
- load multiple local subtitle candidates;
- fetch candidates from Kitsunekko by AniList or TVDB ID;
- fall back to browsing the series files when an episode lookup fails;
- read SRT and ASS candidates;
- show the same candidate metadata;
- promote the selected candidate to `{media_stem}.srt`.

It does not mean identical layouts, keybindings, playback, timeline behavior,
or selection state.

## What Exists Today

The TUI has the complete workflow, but much of its non-UI code is in
`packages/frontend/src/ja_media_frontend/subsync_tui.py`.

The reader only loads one SRT. Its Python server builds a static
`ReaderSession`, and its JavaScript handles display and playback.

`packages/core` already contains:

- `SubtitleCue`, SRT parsing, SRT formatting, and retiming in
  `ja_media_core.transcripts`;
- the Kitsunekko client and its predictable response schema in
  `ja_media_core.kitsunekko`.

The missing work is mostly moving existing subtitle utilities out of the TUI
and then calling them from both frontends.

## Move These Existing Pieces

### Move to `ja_media_core.transcripts`

Move these functions from `subsync_tui.py`:

- `parse_ass()`
- `parse_ass_timestamp()`
- `clean_ass_text()`

Add:

```python
def read_subtitle(path: str | Path) -> list[SubtitleCue]:
    """Read SRT or ASS based on the file suffix."""
```

That is enough format abstraction. Do not add a parser registry or format
plugin API.

The ASS parser is intentionally lossy: it keeps dialogue timing and plain text
and discards typesetting. Document that in its docstring.

### Move to a new `ja_media_core.subsync`

Move `SubtitleTrack` and rename it to `SubtitleCandidate`:

```python
@dataclass(frozen=True)
class SubtitleCandidate:
    path: Path
    cues: tuple[SubtitleCue, ...]
    repo_path: str | None = None
    subtitle_id: str | None = None

    @property
    def label(self) -> str:
        return self.repo_path or self.path.name

    @property
    def active_s(self) -> float:
        return sum(max(0.0, cue.end_s - cue.start_s) for cue in self.cues)

    @property
    def end_s(self) -> float:
        return max((cue.end_s for cue in self.cues), default=0.0)
```

No `origin`, generic metadata bag, or session object is needed right now.
Local/remote provenance is already represented by `path`, `repo_path`, and
`subtitle_id`.

Move `runtime_episode_number()` and `_first_positive_int()`:

```python
def infer_episode_number(media_path: str | Path) -> int | None:
    ...
```

Do not invent a `MediaHints` model until title or season hints are actually
used. The current consumer needs one integer.

Move and generalize `resolve_srt_inputs()`:

```python
def resolve_subtitle_inputs(inputs: Iterable[str]) -> list[Path]:
    """Expand paths and quoted globs, validate SRT/ASS, and deduplicate."""
```

Move the reader's sidecar discovery into the same module:

```python
def discover_subtitle_files(media_path: str | Path) -> list[Path]:
    """Return exact-stem and then deterministic fuzzy sibling matches."""
```

The caller chooses what to do with the result:

- the reader can load all matches;
- a strict CLI can reject multiple matches;
- the TUI can append only the exact sidecar if that remains the desired UX.

Also add:

```python
def load_subtitle_candidate(
    path: str | Path,
    *,
    repo_path: str | None = None,
    subtitle_id: str | None = None,
) -> SubtitleCandidate:
    ...
```

This calls `read_subtitle()` and computes no UI state.

### Move remote-file materialization

Move `_remote_file_is_subtitle()` and `_track_from_remote_file()` out of the
TUI:

```python
def is_supported_subtitle_file(file: Mapping[str, object]) -> bool:
    ...


def download_subtitle_candidate(
    client: KitsunekkoSubtitlesClient,
    file: Mapping[str, object],
    download_dir: str | Path,
) -> SubtitleCandidate:
    ...
```

`download_subtitle_candidate()` should:

1. require `subtitle_id`;
2. call `client.file_content(subtitle_id)`;
3. write the original downloaded bytes into the session temp directory;
4. parse SRT or ASS through `read_subtitle()` or the equivalent content parser;
5. return `SubtitleCandidate` with `repo_path` and `subtitle_id`.

Do not add another remote-file DTO just to copy fields out of the existing
service response. The Kitsunekko schema is already owned by this repo and is
predictable. If its response shape becomes painful, fix
`KitsunekkoFileListResponse` directly.

### Move remote-file filtering

The full-series picker needs searchable rows. Move only the pure search:

```python
def search_subtitle_files(
    files: Iterable[Mapping[str, object]],
    query: str,
) -> list[Mapping[str, object]]:
    ...
```

This absorbs:

- `_remote_file_search_text()`
- `_remote_file_match_sort_key()`
- `_fuzzy_score()`

Keep `_remote_file_option_label()` in the TUI because it returns Rich `Text`.
The browser will render the same result rows as HTML.

### Move safe sidecar writing

Move:

- `_same_file()`
- `_copy_file_contents_atomic()`

Expose:

```python
def sidecar_path(media_path: str | Path) -> Path:
    return Path(media_path).with_suffix(".srt")


def promote_subtitle(
    candidate: SubtitleCandidate,
    media_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    ...
```

Behavior:

- return without writing when the candidate already is the destination;
- refuse to replace an existing destination unless `overwrite=True`;
- atomically copy an SRT candidate without preserving source metadata;
- format normalized cues as SRT when the source candidate is ASS.

The overwrite confirmation UI remains in each frontend. Core only enforces the
flag.

## Do Not Abstract These

### Kitsunekko method selection

Do not add a generic lookup service or `SubtitleLookupResult`.

The application already knows which selector the user supplied. Calling one of
these methods is explicit and fine:

```python
if anilist_id is not None:
    response = client.anilist_episode_files(anilist_id, episode_number)
else:
    response = client.tvdb_episode_files(
        tvdb_id,
        episode_number,
        media_kind=tvdb_kind,
    )
```

The same applies to `anilist_files()` versus `tvdb_files()` for the full-series
fallback. This branch is not a reusable domain algorithm.

### Settings and override precedence

Do not hide settings loading inside a subtitle workflow object.

The concrete precedence should be:

1. application/CLI arguments;
2. project settings;
3. environment overrides handled by Pydantic Settings.

The application constructs `HttpKitsunekkoSubtitlesClient` with the resolved
base URL. The client performs HTTP. Subsync helpers accept the client as an
argument.

Cleaning up the existing hand-rolled environment lookup in
`ja_media_core.kitsunekko` may be worthwhile, but it is a configuration cleanup,
not required for reader/TUI parity.

### Lookup fallback presentation

The shared fact is simply whether the episode request returned usable subtitle
rows.

The application handles the flow:

```python
episode_files = [...]
if episode_files:
    # Offer/load them.
else:
    series_files = [...]
    # Open the TUI picker or browser picker.
```

The HTTP client should expose status codes cleanly instead of requiring the TUI
to search for `"404"` in an exception string. Fixing that exception is useful.
It does not require a lookup orchestration layer.

### Candidate list state

Keep these independently in the TUI and browser:

- selected candidate index;
- selected cue per candidate;
- where newly downloaded candidates appear;
- timeline position and zoom;
- pending lookup or playback status text.

Candidate ordering can stay as straightforward application code. If both
frontends later need exactly the same ordering policy, extract a ten-line
helper then.

### Playback and rendering

Leave all of this alone:

- TUI PCM decoding, `sounddevice`, playback threads, and terminal timeline;
- browser byte-range serving, `<audio>`, DOM cue list, fonts, and CSS;
- keybindings, `gg`, paging, zooming, clipboard, notifications, and modals;
- clock/duration formatting.

These consume candidate cues but are not subtitle-management logic.

## Exact Reader Work After Extraction

### Python server

Change reader startup from one `sub_file` to zero or more subtitle inputs plus
the existing remote selectors:

```text
ja-media subsync reader MEDIA [SUBTITLE ...]
    [--anilist ID | --tvdb ID]
    [--episode NUMBER]
    [--tvdb-kind KIND]
    [--fetch-subs]
```

Startup does:

1. resolve the media path;
2. resolve explicit subtitle paths/globs;
3. discover local sidecars when appropriate;
4. load each path with `load_subtitle_candidate()`;
5. infer the episode only when `--episode` is absent;
6. optionally fetch episode rows and materialize candidates;
7. create the browser app with the candidate list and session temp directory.

Add server operations for:

- returning the current candidate list and cues;
- fetching episode rows after the page is open;
- returning full-series rows for the fallback picker;
- downloading one selected remote row;
- promoting one loaded candidate.

These routes should call the concrete core functions above. The route schema can
use the existing Kitsunekko row shape; it does not need a second set of domain
models.

### Browser JavaScript

Add:

- a candidate list;
- candidate switching;
- AniList/TVDB ID and episode inputs;
- a fetch button;
- a full-series search/picker when no episode rows are available;
- a promote button with overwrite confirmation.

When the selected candidate changes, replace the cue list and subtitle timeline
with that candidate's cues. Existing cue navigation, timeline, and `<audio>`
playback remain local JavaScript.

## Exact TUI Work After Extraction

The TUI keeps its current behavior and replaces local implementations:

| Current TUI code | Replacement |
| --- | --- |
| `SubtitleTrack` | `SubtitleCandidate` |
| `runtime_episode_number()` | `infer_episode_number()` |
| `resolve_srt_inputs()` | `resolve_subtitle_inputs()` |
| direct `read_srt()` | `load_subtitle_candidate()` |
| `parse_ass()` family | `ja_media_core.transcripts` |
| `_remote_file_is_subtitle()` | `is_supported_subtitle_file()` |
| `_track_from_remote_file()` | `download_subtitle_candidate()` |
| remote fuzzy helpers | `search_subtitle_files()` |
| `_copy_file_contents_atomic()` | `promote_subtitle()` |

The TUI still:

- reads modal values;
- decides which concrete Kitsunekko method to call;
- opens the full-series picker when needed;
- inserts downloaded candidates into its list;
- asks before overwrite;
- renders and plays the result.

## Implementation Order

### 1. Subtitle formats and local candidates

- Move ASS parsing.
- Add `read_subtitle()`.
- Add `SubtitleCandidate`.
- Move episode inference.
- Move input and sidecar resolution.
- Update TUI and reader to use those functions.

This removes duplicated local-file behavior first.

### 2. Remote candidate helpers

- Move supported-file filtering.
- Move remote download/materialization.
- Move pure full-series search.
- Improve the HTTP exception to expose its status code.
- Update the TUI without changing its screens.

At this point all Kitsunekko data work is callable outside Textual.

### 3. Sidecar promotion

- Move atomic writing.
- Add `promote_subtitle()`.
- Keep confirmation in Textual.
- Add the reader promotion route and browser confirmation.

### 4. Browser parity UI

- Add multiple candidates.
- Add lookup controls and episode inference.
- Add full-series picker.
- Add candidate promotion.

## Tests

Move tests with the code they test.

Core tests:

- episode inference;
- path/glob expansion and deduplication;
- exact and fuzzy sidecar discovery;
- SRT and ASS loading;
- candidate statistics;
- supported remote-file filtering;
- remote SRT and ASS download;
- remote search ordering;
- sidecar no-op, overwrite refusal, atomic copy, and ASS-to-SRT promotion.

TUI tests:

- modal values cause the expected concrete Kitsunekko call;
- returned rows become visible candidates;
- fallback opens the picker;
- overwrite confirmation passes the correct `overwrite` value;
- rendering and playback still work.

Reader tests:

- startup returns multiple candidates;
- fetch and fallback routes call the expected concrete client method;
- downloading a row adds a candidate;
- promotion reports an existing sidecar conflict;
- byte-range media serving still works.

## Resulting Files

```text
packages/core/src/ja_media_core/
├── transcripts.py       # SRT and ASS parsing/formatting/retiming
├── kitsunekko.py        # settings-backed HTTP client and service response
├── subsync.py           # candidate/file/download/promotion helpers
└── reader.py            # browser JSON/timeline payloads, if still useful

packages/frontend/src/ja_media_frontend/
├── subsync_tui.py       # Textual state, calls, rendering, PCM playback
├── subsync_reader.py    # FastAPI routes, media serving, browser launch
└── static/
    ├── reader.js        # browser state, rendering, browser playback
    ├── reader.html
    └── reader.css
```

This is the whole boundary: core reads, downloads, normalizes, describes, and
writes subtitle candidates. Each frontend gathers inputs, makes the explicit
service call, keeps its own interaction state, and renders the candidates.
