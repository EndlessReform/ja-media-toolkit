# VAD Visualization in Subsync TUI

## Objective
Integrate Voice Activity Detection (VAD) visualization into the `subsync` TUI. This allows users to compare the predicted active spans of a candidate SRT file against the actual predicted voice activity from the audio backend (e.g., Silero). This comparison facilitates:
1. **Candidate Selection**: Visually identifying which candidate SRT best aligns with the actual speech.
2. **Manual Alignment**: Guiding the manual adjustment of an SRT's timing (e.g., via a fixed offset) by providing a "ground truth" speech map.

## Current State
- `SubsyncTuiApp` uses `TimelineWidget` to display subtitles.
- `TimelineWidget` currently renders a single sequence of `TimedSpan` objects on one activity bar.
- VAD abstractions exist in `ja_media_core.vad` (Protocol `VadBackend`) and are implemented in `ja_media_apple`.

## Proposed Changes

### 1. `TimelineWidget` Extensions
The `TimelineWidget` needs to transition from a single-timeline view to a multi-layered view.

**Modified `set_timeline` signature:**
```python
def set_timeline(
    self,
    layers: dict[str, Sequence[TimedSpan]], # e.g., {"vad": [...], "srt": [...]}
    *,
    start_s: float,
    duration_s: float,
    title: str = "Timeline",
    active_span: TimedSpan | None = None,
) -> None:
```

**Implementation Details:**
- `render_timeline` should iterate over the `layers` dictionary and generate one `activity_bar` per layer.
- `activity_bar` should be refactored to take a specific sequence of spans and a label/style configuration.
- Use distinct colors for different layers (e.g., VAD in a subtle grey/blue, SRT in green/cyan).

### 2. `SubsyncTuiApp` Integration
The app must coordinate the VAD backend and the TUI state.

**VAD Backend Integration:**
- Add a `VadBackend` (via a factory or injection) to `SubsyncTuiApp`.
- On mount or audio source change, run `vad_backend.detect([self.audio_source])`.
- Cache the resulting `VadTimeline` to avoid redundant processing.

**Data Flow in `refresh_view`:**
```python
def refresh_view(self) -> None:
    # ...
    layers = {
        "Speech": self.vad_timeline.speech if self.vad_timeline else (),
        "Subtitle": self.track.cues if self.tracks else (),
    }
    timeline.set_timeline(
        layers=layers,
        start_s=self.window_start_s,
        duration_s=self.window_s,
        # ...
    )
```

### 3. Manual Alignment Support (Feasibility)
To support "fixed offset" alignment:
- The TUI already provides `z` and `x` bindings that call `shift_track_timing` ($\pm 100\text{ms}$).
- Currently, `shift_track_timing` modifies the cue timings in-place. To improve UX, we may transition this to a non-destructive `self.track_offsets: dict[int, float]` that is applied during rendering.
- In `refresh_view`, "virtual" `TimedSpan` objects for the SRT layer would be created by applying this offset to `cue.start_s` and `cue.end_s`.

### 4. VAD Visual Identity
To distinguish the VAD "baseline" from the multicolor candidate subtitles, we will use a monochromatic style and a distinct character. This ensures visibility across both light and dark terminal themes.

**Candidates:**
- **The "Floor"**: `▄` (Lower half block) + `bright_black` (Grey). Mirror image of the subtitle block.
- **The "Dots"**: `▫` (Small white square) + `white`. Discrete markers.
- **The "Ticks"**: `╎` (Vertical line) + `bright_black` (Grey). Low-profile reference.
- **The "Dashes"**: `╌` (Double dashed line) + `bright_black` (Grey). Subtle connectivity.

**Recommendation**: The "Floor" (`▄` / `bright_black`) is preferred as it provides the strongest geometric contrast to the subtitle blocks (`▀`).

## Feasibility Analysis
- **Computational Overhead**: VAD detection for a full episode can be slow. We should either:
    - Use a background thread/task to process VAD.
    - Process VAD in chunks as the user scrolls (though this creates lag).
    - Pre-calculate VAD once and cache it.
- **Visual Space**: Adding multiple bars to the `TimelineWidget` will consume more vertical space. The `activity_bar` is currently a single line of blocks, so adding 1-2 more lines is negligible.
- **API Compatibility**: The `TimedSpan` protocol is already generic enough to handle both `SubtitleCue` and `SpeechSpan`.

## Proposed API Stubs

### `TimelineWidget`
```python
def render_layers(self, layers: dict[str, Sequence[TimedSpan]], width: int, start_s: float, end_s: float) -> list[Text]:
    """Returns a list of activity bars, one for each layer."""
    ...

def activity_bar(self, label: str, spans: Sequence[TimedSpan], width: int, start_s: float, end_s: float) -> Text:
    """Renders a single labeled activity bar."""
    ...
```

### `SubsyncTuiApp`
```python
def _ensure_vad_processed(self) -> None:
    """Trigger VAD detection if not already cached for the current source."""
    if self.vad_cache_key != self.audio_source.source_path:
        self.vad_timeline = self.vad_backend.detect([self.audio_source])[0]
        self.vad_cache_key = self.audio_source.source_path
```
