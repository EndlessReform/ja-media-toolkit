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

## Options

| Option | Long Option | Default | Description |
| :--- | :--- | :--- | :--- |
| `-n` | `--top-k` | `3` | Number of results to return. |
| | `--include-movies` | `false` | Include movies in search results. |
| | `--include-ova` | `false` | Include OVA entries in search results. |
| | `--all-formats` | `false` | Include all anime formats (specials, music, etc.). |
| | `--format` | `table` | Output format: `table` or `json`. |
