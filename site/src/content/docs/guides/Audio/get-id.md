---
title: Get Anime ID
description: Search for anime by title and retrieve AniList IDs using the ja-media CLI.
---

The `get-id` command allows you to quickly search for anime titles and retrieve their corresponding AniList IDs. This is useful for feeding IDs into other tools like `subsync` or `transcribe`.

## Usage

```sh
ja-media get-id <query> [options]
```

### Examples

**Simple search:**
```sh
ja-media get-id "Steins;Gate"
```

**Search for more results in JSON format:**
```sh
ja-media get-id "One Piece" -n 10 --format json
```

**Include movies and OVAs:**
```sh
ja-media get-id "Fate" --include-movies --include-ova
```

**Add metadata fields to candidates:**
```sh
ja-media get-id "Aria" -n 5 --field popularity --field averageScore --format json
```

**Resolve a title inventory file:**
```sh
ja-media get-id -f blogspot-reviews.csv -n 5 --field popularity --field siteUrl
```

For `.txt`, `.csv`, `.jsonl`, and `.ndjson` inputs, `-f` runs analytical batch
mode and writes `<input-stem>.anilist.jsonl`. Text files use one non-empty title
per line. CSV and JSONL inputs preserve existing columns and append an
`anilist_candidates` array. CSV/JSONL records resolve the title from `query`,
`title`, `name`, `anime_title`, `review_title`, or common AniList title columns.

## Options

| Option | Long Option | Default | Description |
| :--- | :--- | :--- | :--- |
| `-n` | `--top-k` | `3` | Number of results to return. |
| `-f` | `--file` | None | Parse a media filename, or batch `.txt`/`.csv`/`.jsonl`/`.ndjson` inputs. |
| | `--field` | None | Extra public AniList metadata field to add to candidates; repeatable. |
| | `--include-movies` | `false` | Include movies in search results. |
| | `--include-ova` | `false` | Include OVA entries in search results. |
| | `--all-formats` | `false` | Include all anime formats (specials, music, etc.). |
| | `--force-anilist` | `false` | Query AniList directly instead of the local BM25 mirror. |
| | `--format` | `table` | Output format: `table` or `json`. |
