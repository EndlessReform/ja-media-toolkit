---
title: Configuration
description: Managing global settings and service discovery for the ja-media toolkit.
---

The `ja-media` toolkit uses a TOML configuration file to manage settings across different tools and runtimes. This allows you to define your home-lab service URLs and model paths once, rather than setting environment variables for every individual tool.

## Config File Location

By default, the toolkit looks for a configuration file in the following order of priority:

1.  **Explicit Path**: Provided via a `-c` or `--config` flag in the CLI.
2.  **Environment Variable**: The path specified in the `JA_MEDIA_CONFIG` environment variable.
3.  **XDG Default**: `~/.config/ja-media-toolkit/config.toml` (on Linux/macOS).

## Global Service Discovery

### The `[services]` Section

Add a `[services]` section to your `config.toml` to define the base URL for your LAN services:

```toml
[services]
root_url = "http://ja-media.local"
```

When this is set, clients (like the Kitsunekko subtitle client) will automatically use this as their base URL if no other override is provided. This means you can host multiple services behind a single Caddy or Nginx proxy and manage only one URL.

### Overriding Service URLs

If a specific service is hosted on a different machine or port, you can still override the global root using environment variables. For example, for Kitsunekko subtitles:

```sh
export KITSUNEKKO_SUBTITLES_BASE_URL="http://192.168.1.50:8000"
```

The indexed anime-audio client uses the same gateway root plus
`/api/v1/audio`. Its direct-service override is `ANIME_AUDIO_BASE_URL`.

## Backend Configuration

The configuration file also supports detailed backend settings. While the `[services]` section handles discovery, the `[asr]` section (and others) allows you to tune the actual ML runtimes.

```toml
[asr]
default_backend = "vibevoice_vllm_local"

[asr.backends.vibevoice_vllm_local]
type = "vibevoice_vllm"
vllm_base_url = "http://gpu-server:8000"
# ... other backend-specific settings
```

Refer to the [Transcription Guide](/guides/audio/transcribe) for a detailed breakdown of ASR configuration options.

## Subtitle Language Identification

Subtitle tools can share a lightweight language-identification policy through
`[subtitles.language_id]`. The classifier first measures Unicode scripts and
only runs sampled FastText identification for ambiguous files. Its result is a
ranking-oriented bucket: Japanese, unknown, bilingual, non-Japanese, or
insufficient text.

The defaults favor recall: uncertain candidates remain available, while
bilingual and non-Japanese subtitles sort later. Override only the thresholds
that need tuning:

```toml
[subtitles.language_id]
# Sample evenly across the complete subtitle when script evidence is ambiguous.
sample_lines = 50
min_line_characters = 5

# Candidate selection is intentionally more permissive than corpus cleaning.
japanese_lid_ratio = 0.60
bilingual_lid_ratio = 0.15

# Skip model LID for subtitles with a sufficiently strong Japanese profile.
obvious_japanese_script_ratio = 0.70
obvious_kana_ratio = 0.08
obvious_max_foreign_script_ratio = 0.15
```

Call `analyze_srt_language(path, config=settings.subtitles.language_id)` for a
file, or `analyze_subtitle_language(cues, ...)` when SRT cues are already
loaded. The returned analysis includes script metrics, sampled language ratios,
an explanation, and a `sort_key` suitable for best-first candidate ordering.
