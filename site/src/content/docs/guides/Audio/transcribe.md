---
title: Transcription
description: Using the ja-media ASR (Automatic Speech Recognition) tools for Japanese audio.
---

The `transcribe` command is the primary entry point for generating transcripts from audio.

## Basic Usage

If you have installed the CLI via [Setup Tools](/setup/tools), you can run transcription from any directory:

```sh
ja-media transcribe input.wav --language ja --format text
```

### Long Audio and Batching

For longer files or batches of media, quote your glob patterns to ensure consistent sorting:

```sh
ja-media transcribe "../../input/*.mp3" --language ja --format json
```

### Generating Subtitles

To generate `.srt` files, use the `--srt-dir` flag. The command will write one `.srt` file per input audio file:

```sh
ja-media transcribe "input/*.mp3" --language ja --srt-dir ./output/srt --format text
```

### Advanced Options

| Flag | Description |
|---|---|
| `--startup-only` | Smoke-test config and model loading without calling the ASR backend. |
| `--max-concurrent-requests` | Override the config to limit how many requests are sent to the backend simultaneously. |
| `--start-s` / `--end-s` | Transcribe only a specific time window (useful for debugging). |
| `--language` | Specify the target language (e.g., `ja` or `en`). |
| `--format` | Output format: `text` or `json`. |

---

## Backends

The `transcribe` tool is designed to be backend-agnostic, allowing it to work with various ASR implementations.

### Hybrid (VibeVoice + vLLM)

The current primary implementation uses a decoupled, hybrid architecture to balance local control with remote compute.

#### The Hybrid Workflow
Rather than sending raw audio to a server, the hybrid backend splits the work:

1.  **Local Planning (Front-end)**: The local machine uses a VibeVoice encoder and Voice Activity Detection (VAD) to analyze the audio. It identifies speech boundaries and slices the audio into optimal "chunks" based on your config.
2.  **Remote Decoding (Back-end)**: The local machine converts these chunks into "prompt embeddings" and sends them to a remote ASR backend (vLLM). The backend performs the heavy lifting of decoding these embeddings into text.

This split ensures that timing and chunking are handled precisely on your local machine, while the GPU-heavy LLM inference is offloaded.

**Key Benefit: VRAM Efficiency**
By handling the audio encoding locally, the remote GPU only needs to load the language model (LM) rather than the entire encoder-decoder stack. This significantly reduces VRAM requirements—making it possible to run high-quality transcription on consumer gaming cards or L40s—and removes the need for specialized VibeVoice forks of vLLM.


#### Configuration
You can manage these settings in your [global config](/setup/config):

```toml
[asr]
default_backend = "vibevoice_vllm_local"

[asr.backends.vibevoice_vllm_local]
type = "vibevoice_vllm"
vllm_base_url = "http://gpu-server:8000"
vllm_model = "/models/vibevoice"
checkpoint = "jkeisling/vibevoice-encoder-only"
device = "cpu"
dtype = "float32"
load_on_startup = true
vad_model_id = "mlx-community/silero-vad"
target_split_s = 300
split_search_radius_s = 45
prefer_split_before_target = false
rejoin_overlap_s = 0
timeout_s = 12000
max_concurrent_requests = 4
max_output_tokens = 2048
temperature = 0.0
top_p = 1.0
```

#### Running the ASR Backend (vLLM)
To use this backend, you must run a vLLM server with the VibeVoice LM. To avoid re-downloading models and to use your system's Hugging Face cache, use the following Docker command:

```sh
docker run --runtime nvidia --gpus all \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v ./out/textonly-checkpoint:/models/vibevoice \
  -p 8000:8000 \
  --ipc=host \
  vllm/vllm-openai:latest \
  jkeisling/vibevoice-asr-lm-trunk \
  --enable-prompt-embeds
```

#### Tuning Audio Splitting
Because the hybrid backend relies on local planning, you can tune how the audio is sliced in your config:

- `target_split_s`: The ideal segment length (e.g., `300` for 5 minutes).
- `split_search_radius_s`: How far (in seconds) the tool looks around the target for a silence boundary.
- `rejoin_overlap_s`: Controls how segments are merged back together.
