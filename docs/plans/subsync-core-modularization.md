# Subsync Reader Parity: Remaining Tasks

## Goal
Complete the browser-based subtitle reader's parity with the TUI, allowing users to fetch and promote subtitle candidates from the web interface.

## Open Items

### 1. Reader API (`packages/frontend/src/ja_media_frontend/subsync/reader.py`)
Implement FastAPI routes to expose the shared `subsync` services:
- **Candidate Retrieval**: Route to list local candidates and fetch remote ones (Kitsunekko).
- **Promotion**: Route to call `promote_subtitle` for the selected candidate.

### 2. Reader UI (`packages/frontend/src/ja_media_frontend/static/reader.js`)
Implement the frontend controls to interact with the new API:
- **Candidate List**: A UI component to display available `.srt`/`.ass` files.
- **Fetch Controls**: Buttons to trigger remote lookup and materialization.
- **Promote Action**: A button to promote the selected candidate to `{media_stem}.srt`.
