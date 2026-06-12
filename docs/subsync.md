# Subsync TUI

`subsync tui` is the first-pass subtitle timing review shell. It is meant for
quickly answering: "does this `.srt` line up with this media file well enough
to use or repair?"

The current version does not run VAD scoring or automatic retiming yet. It
loads one media file plus one or more `.srt` candidates, shows subtitle activity
on a text timeline, and lets you listen to exact cue boundaries.

## Run It

Work from the Apple environment:

```sh
cd envs/apple
uv run ja-media subsync tui ../../examples/input/example_走る高級レストランに乗ってきた.mp3 '../../subs/*.srt'
```

You can pass a single `.srt` path instead of a glob:

```sh
uv run ja-media subsync tui ../../media/episode.mkv ../../subs/episode.ja.srt
```

Quote glob patterns so the CLI can expand, sort, deduplicate, and validate the
candidate files itself.

## What You See

The TUI has four main regions:

- source media path
- candidate `.srt` table with cue count, current cue, active subtitle time, and
  total subtitle span
- subtitle activity timeline for the selected candidate
- currently selected cue text

The timeline uses colored half-blocks for subtitle spans and blank space for
gaps. The selected cue is highlighted, which makes edges easier to inspect when
combined with playback.

## Key Bindings

| Key | Action |
| --- | --- |
| `space` | Play or stop the current cue from the media file |
| `h` / `l` | Previous / next cue in the selected `.srt` |
| `j` / `k` | Previous / next `.srt` candidate |
| `gg` / `G` | Jump to start / end |
| `Ctrl-f` / `Ctrl-b` | Page the visible timeline forward / backward |
| `Ctrl-d` / `Ctrl-u` | Half-page the visible timeline forward / backward |
| `+` or `=` | Zoom in, showing fewer seconds |
| `-` or `_` | Zoom out, showing more seconds |
| `q` | Quit |

Playback uses `ffplay` through `subprocess`, not a Python audio library. The
clip starts exactly at the current cue start and stops exactly at the current
cue end, so bad subtitle edges are audible. Moving with `h` or `l` stops active
playback before selecting the next cue.

## Current Limits

- No automatic candidate scoring yet.
- No VAD-vs-subtitle comparison yet.
- No offset nudging or sidecar write flow yet.
- Playback depends on `ffplay` being available on `PATH`.

Those limits are intentional for the first shell: it is already useful as a
manual timing review surface, and the later scoring/retiming work can plug into
the same parsed cue data and TUI layout.
