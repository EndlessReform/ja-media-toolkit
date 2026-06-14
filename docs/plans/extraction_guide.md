# Extracting Season and Episode from Anime Filenames

This guide explains how to extract metadata like `season` and `episode` from `.srt` or video filenames using the `PTN` (Parse Torrent Name) library.

## 🛠 Installation

This project is managed with `uv`. To install all dependencies (including `parse-torrent-title` and `polars`) and set up the environment:

```bash
uv sync
```

To run scripts:
```bash
uv run python your_script.py
```

If installing via pip manually:
```bash
pip install parse-torrent-title polars
```

## 🚀 Implementation

The core logic involves stripping the file extension and passing the filename to `PTN.parse()`.

### Basic Example
```python
import PTN
import os

filename = "[shincaps] Aharen-san wa Hakarenai season2 - 07 (AT-X 1440x1080 MPEG2 AAC).srt"

# 1. Remove extension
name_without_ext = os.path.splitext(filename)[0]

# 2. Parse using PTN
parsed = PTN.parse(name_without_ext)

print(parsed)
# Output: {'season': 2, 'episode': 7, 'title': 'Aharen-san wa Hakarenai', ...}

season = parsed.get('season')
episode = parsed.get('episode')
print(f"Season: {season}, Episode: {episode}")
```

### Handling the Dataset (Bulk Processing)
If you are processing thousands of files, it is recommended to use a `ProcessPoolExecutor` as `PTN` parsing can be CPU-intensive.

## 📊 Coverage & Quality Analysis

We performed a sanity check on a dataset of **120,097** files to see how reliable these extractions are.

### Findings
- **Sanity Rate:** ~99.58%
- **Fucked-up Rate:** ~0.42% (511 records)

The vast majority of filenames are parsed perfectly into integers. The rare "garbage" cases usually fall into two categories:
1. **Multi-episode files:** Filenames like `E21-E22` result in strings like `"21, 22"`.
2. **Parsing Leaks:** Rare bugs in cumulative state during bulk processing.

### Validation Query
To calculate this coverage, the following DuckDB query was used on the resulting parquet file:

```sql
SELECT 
    COUNT(*) as total,
    COUNT(CASE WHEN (TRY_CAST(season AS INTEGER) IS NOT NULL OR season IS NULL) 
              AND (TRY_CAST(episode AS INTEGER) IS NOT NULL OR episode IS NULL) 
         THEN 1 END) as sane,
    COUNT(CASE WHEN (TRY_CAST(season AS INTEGER) IS NULL AND season IS NOT NULL) 
              OR (TRY_CAST(episode AS INTEGER) IS NULL AND episode IS NOT NULL) 
         THEN 1 END) as fucked_up
FROM 'enriched_titles.parquet';
```
