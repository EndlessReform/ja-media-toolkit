# Batch ASR with `vllm run-batch` and Docker

This guide shows one practical way to transcribe about 100 earnings calls with
vLLM's OpenAI batch-file runner. It focuses on throughput, Docker, audio file
ingestion, and smoke tests for both the dedicated transcription endpoint and
audio-capable chat completions.

Status checked against this repository on 2026-05-09.

## Quick Answers

### Does vLLM resample or convert audio formats?

Yes, for the dedicated transcription path. The server decodes the uploaded
audio bytes with `soundfile`, falling back to PyAV/FFmpeg for formats such as
MP4/M4A/WebM, then resamples to the model's `SpeechToTextConfig.sample_rate`.
You normally do not need to pre-resample to 16 kHz yourself.

Preprocessing is still useful when you care about predictable throughput:

- Normalize huge or strange containers to WAV/FLAC/MP3 ahead of time.
- Split very long calls yourself if the model has a short maximum clip length,
  or rely on vLLM's chunking only when the model's STT config enables it.
- Keep file sizes below `VLLM_MAX_AUDIO_CLIP_FILESIZE_MB`; the default is 25 MB.
  Earnings calls often exceed that, so set this env var or pre-compress/split.

Relevant local code/docs:

- `vllm/entrypoints/openai/speech_to_text/speech_to_text.py`
- `vllm/multimodal/media/audio.py`
- `docs/contributing/model/transcription.md`
- `docs/serving/openai_compatible_server.md`

### Do batch inputs need an HTTP server for audio files?

Not strictly, but there is no `file://` or plain local path support inside the
batch JSONL schema.

For `vllm run-batch`, each audio request uses `file_url`, not `file`. Supported
schemes are:

- `http://...`
- `https://...`
- `data:audio/...;base64,...`

So your options are:

- Use a tiny local HTTP server or object-store/presigned URLs. This is usually
  the cleanest option for 100 earnings calls.
- Embed audio as base64 data URLs. This avoids HTTP boilerplate, but bloats the
  JSONL by roughly 33% and the runner reads the full input file into memory.

For large batches, prefer HTTP URLs. For quick smoke tests, data URLs are great.

### Are MSFT VibeVoice and Qwen3-ASR supported here?

Qwen3-ASR is supported by vLLM in this repo. The supported-model table lists
`Qwen3ASRForConditionalGeneration`, and the Qwen recipe shows both
`/v1/chat/completions` and `/v1/audio/transcriptions` usage with
`Qwen/Qwen3-ASR-1.7B`.

VibeVoice-ASR is not listed in this repo's built-in supported-model table. The
Microsoft VibeVoice docs describe a separate vLLM plugin that serves VibeVoice
through the standard `/v1/chat/completions` endpoint, with hotwords and long
audio support. Treat VibeVoice as "supported via Microsoft's plugin", not as a
drop-in model for this repo's built-in `vllm run-batch` transcription endpoint
unless you install and run that plugin.

References:

- vLLM supported models: `docs/models/supported_models.md`
- Qwen3-ASR recipe: https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3-ASR.html
- VibeVoice plugin doc: https://github.com/microsoft/VibeVoice/blob/main/docs/vibevoice-vllm-asr.md
- VibeVoice model card: https://huggingface.co/microsoft/VibeVoice-ASR

### Can I do keyword biasing or mixed prompt + audio?

There are two paths:

1. Dedicated transcription endpoint: `/v1/audio/transcriptions`

   This accepts audio plus fields such as `language`, `prompt`, `hotwords`,
   `response_format`, and sampling parameters. Whether `prompt` or `hotwords`
   actually affects output depends on the model implementation. In this repo,
   Qwen3-ASR's built-in transcription prompt currently uses the audio and
   optional `to_language`, but does not obviously inject request `prompt` or
   `hotwords` into its built-in `get_generation_prompt`.

2. Audio chat completions: `/v1/chat/completions`

   This is more flexible for mixed text plus audio. You can send a text part
   containing entity lists, formatting instructions, ticker symbols, Japanese
   names, etc., plus an `audio_url` or `input_audio` part. Use this when you
   want model-specific instruction following rather than strict ASR endpoint
   compatibility. It requires an audio-capable chat model, such as Qwen3-ASR,
   Qwen2-Audio, Qwen3-Omni, etc.

## Recommended Shape for 100 Earnings Calls

Use the dedicated batch transcription endpoint first:

- Start one Docker container that runs `vllm run-batch`.
- Serve audio files via HTTP from the host, a sidecar container, or object
  storage.
- Generate one JSONL request per call or per pre-split segment.
- Use `response_format: "json"` for simple text or `verbose_json` when supported
  and you want segments/timing fields.
- Set `language: "en"` for English calls and `language: "ja"` for Japanese calls
  when the model supports those codes.

For maximum throughput on many GPUs, consider data parallelism or launching
multiple independent batch jobs, each with its own shard of the JSONL and GPU
assignment. For one GPU, tune `--max-num-seqs`, `--max-num-batched-tokens`, and
`--gpu-memory-utilization` conservatively after a smoke test.

## Docker Setup

Create directories:

```bash
export ASR_ROOT="$PWD/batch-asr"
mkdir -p "$ASR_ROOT/audio" "$ASR_ROOT/out"
```

Put your audio under `$ASR_ROOT/audio`, for example:

```text
batch-asr/audio/
  MSFT-Q1-2026-en.mp3
  SONY-Q4-2026-ja.m4a
  ...
```

Start a small HTTP server from the host:

```bash
cd "$ASR_ROOT/audio"
uv run python -m http.server 8899 --bind 127.0.0.1
```

If Docker on your Linux host cannot reach `127.0.0.1` from inside the
container, use host networking:

```bash
docker run --rm --runtime nvidia --gpus all --network host --ipc=host \
  -e HF_TOKEN="$HF_TOKEN" \
  -e VLLM_MAX_AUDIO_CLIP_FILESIZE_MB=512 \
  -v "$ASR_ROOT:/work" \
  vllm/vllm-openai:latest \
  vllm run-batch \
    -i /work/batch-en-ja-docker-internal.jsonl \
    -o /work/out/results.jsonl \
    --model Qwen/Qwen3-ASR-1.7B \
    --allowed-media-domains 127.0.0.1 localhost \
    --gpu-memory-utilization 0.90
```

If you do not want `--network host`, publish the host server as
`host.docker.internal`:

```bash
docker run --rm --runtime nvidia --gpus all --ipc=host \
  --add-host=host.docker.internal:host-gateway \
  -e HF_TOKEN="$HF_TOKEN" \
  -e VLLM_MAX_AUDIO_CLIP_FILESIZE_MB=512 \
  -v "$ASR_ROOT:/work" \
  vllm/vllm-openai:latest \
  vllm run-batch \
    -i /work/batch-en-ja.jsonl \
    -o /work/out/results.jsonl \
    --model Qwen/Qwen3-ASR-1.7B \
    --allowed-media-domains host.docker.internal \
    --gpu-memory-utilization 0.90
```

The batch runner also supports HTTP/HTTPS input and output JSONL files, so an
S3/GCS/Blob workflow can use presigned GET URLs for input/audio and a presigned
PUT URL for the output.

## Batch JSONL Examples

English dedicated transcription:

```json
{"custom_id":"MSFT-Q1-2026-en","method":"POST","url":"/v1/audio/transcriptions","body":{"model":"Qwen/Qwen3-ASR-1.7B","file_url":"http://127.0.0.1:8899/MSFT-Q1-2026-en.mp3","language":"en","response_format":"json","temperature":0.0}}
```

Japanese dedicated transcription:

```json
{"custom_id":"SONY-Q4-2026-ja","method":"POST","url":"/v1/audio/transcriptions","body":{"model":"Qwen/Qwen3-ASR-1.7B","file_url":"http://127.0.0.1:8899/SONY-Q4-2026-ja.m4a","language":"ja","response_format":"json","temperature":0.0}}
```

English with prompt/hotwords fields:

```json
{"custom_id":"NVDA-Q1-2026-en","method":"POST","url":"/v1/audio/transcriptions","body":{"model":"Qwen/Qwen3-ASR-1.7B","file_url":"http://127.0.0.1:8899/NVDA-Q1-2026-en.wav","language":"en","prompt":"Earnings call transcript. Prefer company and product names: NVIDIA, Blackwell, CUDA, H100, GB200, inference revenue.","hotwords":"NVIDIA,Blackwell,CUDA,H100,GB200,inference revenue","response_format":"json","temperature":0.0}}
```

Japanese with prompt/hotwords fields:

```json
{"custom_id":"TOYOTA-Q1-2026-ja","method":"POST","url":"/v1/audio/transcriptions","body":{"model":"Qwen/Qwen3-ASR-1.7B","file_url":"http://127.0.0.1:8899/TOYOTA-Q1-2026-ja.wav","language":"ja","prompt":"決算説明会の文字起こし。固有名詞を優先してください: トヨタ自動車, ハイブリッド, 電動化, 営業利益, 為替影響。","hotwords":"トヨタ自動車,ハイブリッド,電動化,営業利益,為替影響","response_format":"json","temperature":0.0}}
```

Remember: the endpoint schema accepts `prompt` and `hotwords`, but model support
varies. For Qwen3-ASR, validate with your own smoke tests before relying on
these fields for entity biasing.

## Generating a Batch File

Create a manifest:

```csv
custom_id,path,language
MSFT-Q1-2026-en,MSFT-Q1-2026-en.mp3,en
SONY-Q4-2026-ja,SONY-Q4-2026-ja.m4a,ja
```

Save it as `$ASR_ROOT/manifest.csv`.

Generate JSONL for a host-network Docker run:

```bash
awk -F, 'NR > 1 {
  printf("{\"custom_id\":\"%s\",\"method\":\"POST\",\"url\":\"/v1/audio/transcriptions\",\"body\":{\"model\":\"Qwen/Qwen3-ASR-1.7B\",\"file_url\":\"http://127.0.0.1:8899/%s\",\"language\":\"%s\",\"response_format\":\"json\",\"temperature\":0.0}}\n", $1, $2, $3)
}' "$ASR_ROOT/manifest.csv" > "$ASR_ROOT/batch-en-ja.jsonl"
```

For the `host.docker.internal` networking style, change the URL base:

```bash
sed 's#http://127.0.0.1:8899/#http://host.docker.internal:8899/#' \
  "$ASR_ROOT/batch-en-ja.jsonl" > "$ASR_ROOT/batch-en-ja-docker-internal.jsonl"
```

## No-HTTP Smoke Test with a Data URL

This avoids an HTTP server and proves that batch STT works end to end:

```bash
AUDIO="$ASR_ROOT/audio/MSFT-Q1-2026-en.wav"
B64=$(base64 -w 0 "$AUDIO")
cat > "$ASR_ROOT/smoke-data-url.jsonl" <<EOF
{"custom_id":"smoke-en-data-url","method":"POST","url":"/v1/audio/transcriptions","body":{"model":"Qwen/Qwen3-ASR-1.7B","file_url":"data:audio/wav;base64,$B64","language":"en","response_format":"json","temperature":0.0}}
EOF
```

Run it:

```bash
docker run --rm --runtime nvidia --gpus all --ipc=host \
  -e HF_TOKEN="$HF_TOKEN" \
  -e VLLM_MAX_AUDIO_CLIP_FILESIZE_MB=512 \
  -v "$ASR_ROOT:/work" \
  vllm/vllm-openai:latest \
  vllm run-batch \
    -i /work/smoke-data-url.jsonl \
    -o /work/out/smoke-data-url-results.jsonl \
    --model Qwen/Qwen3-ASR-1.7B
```

Do not use this pattern for many long calls unless you are comfortable with a
very large JSONL file.

## Live Server Smoke Tests

Batch mode is good for throughput, but live requests are the fastest way to
debug model behavior and promptability.

Start an OpenAI-compatible server:

```bash
docker run --rm --runtime nvidia --gpus all --network host --ipc=host \
  -e HF_TOKEN="$HF_TOKEN" \
  -e VLLM_MAX_AUDIO_CLIP_FILESIZE_MB=512 \
  vllm/vllm-openai:latest \
  vllm serve Qwen/Qwen3-ASR-1.7B \
    --host 0.0.0.0 \
    --port 8000 \
    --allowed-media-domains 127.0.0.1 localhost
```

### Smoke Test: Dedicated Transcriptions API, English

```bash
curl -s http://localhost:8000/v1/audio/transcriptions \
  -F model=Qwen/Qwen3-ASR-1.7B \
  -F file=@"$ASR_ROOT/audio/MSFT-Q1-2026-en.mp3" \
  -F language=en \
  -F response_format=json \
  -F temperature=0
```

### Smoke Test: Dedicated Transcriptions API, Japanese

```bash
curl -s http://localhost:8000/v1/audio/transcriptions \
  -F model=Qwen/Qwen3-ASR-1.7B \
  -F file=@"$ASR_ROOT/audio/SONY-Q4-2026-ja.m4a" \
  -F language=ja \
  -F response_format=json \
  -F temperature=0
```

### Smoke Test: Chat Completions with Audio URL, English

Use this when you want text instructions plus audio:

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen3-ASR-1.7B",
    "temperature": 0,
    "max_completion_tokens": 2048,
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "Transcribe this English earnings call excerpt. Bias toward these entity names if acoustically plausible: Microsoft, Azure, Copilot, Intelligent Cloud, Satya Nadella, Amy Hood. Return plain text only."
          },
          {
            "type": "audio_url",
            "audio_url": {
              "url": "http://127.0.0.1:8899/MSFT-Q1-2026-en.mp3"
            }
          }
        ]
      }
    ]
  }'
```

### Smoke Test: Chat Completions with Audio URL, Japanese

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "Qwen/Qwen3-ASR-1.7B",
    "temperature": 0,
    "max_completion_tokens": 2048,
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "この日本語の決算説明会音声を文字起こししてください。音響的に妥当な場合、次の固有名詞を優先してください: ソニーグループ、半導体、イメージセンサー、営業利益、為替影響。出力は文字起こし本文のみ。"
          },
          {
            "type": "audio_url",
            "audio_url": {
              "url": "http://127.0.0.1:8899/SONY-Q4-2026-ja.m4a"
            }
          }
        ]
      }
    ]
  }'
```

### Smoke Test: Chat Completions with Base64 Audio

This is useful when you cannot or do not want to expose an audio URL:

```bash
B64=$(base64 -w 0 "$ASR_ROOT/audio/MSFT-Q1-2026-en.wav")
curl -s http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d "{
    \"model\": \"Qwen/Qwen3-ASR-1.7B\",
    \"temperature\": 0,
    \"max_completion_tokens\": 2048,
    \"messages\": [
      {
        \"role\": \"user\",
        \"content\": [
          {\"type\": \"text\", \"text\": \"Transcribe this earnings-call excerpt. Return plain text only.\"},
          {\"type\": \"input_audio\", \"input_audio\": {\"data\": \"$B64\", \"format\": \"wav\"}}
        ]
      }
    ]
  }"
```

## VibeVoice Notes

Microsoft's VibeVoice-ASR model card says the model supports long-form ASR,
speaker/timestamp/content output, customized hotwords, and more than 50
languages. Their vLLM deployment guide says it uses a plugin and exposes the
standard `/v1/chat/completions` endpoint, not the built-in vLLM
`/v1/audio/transcriptions` path.

That means:

- Do not assume `vllm run-batch` with `url: "/v1/audio/transcriptions"` will
  work for VibeVoice out of the box.
- Do use Microsoft's plugin launcher if VibeVoice-specific features such as
  long single-pass audio, diarization-like structured output, and hotwords are
  the main thing you need.
- For a batch workload, you can still build OpenAI-style JSONL lines targeting
  `/v1/chat/completions` if the plugin is available in the environment that
  runs the batch runner. Validate this first; plugin registration and batch
  runner integration are the moving parts.

Sketch of a VibeVoice-style chat request, based on the plugin's stated
chat-completions interface:

```json
{"custom_id":"MSFT-vibevoice-en","method":"POST","url":"/v1/chat/completions","body":{"model":"microsoft/VibeVoice-ASR","messages":[{"role":"user","content":[{"type":"text","text":"Transcribe this earnings call. Hotwords: Microsoft, Azure, Copilot, Intelligent Cloud, Satya Nadella, Amy Hood."},{"type":"audio_url","audio_url":{"url":"http://127.0.0.1:8899/MSFT-Q1-2026-en.mp3"}}]}],"temperature":0,"max_completion_tokens":4096}}
```

## Operational Tips

- Start with 2 or 3 representative calls before launching all 100.
- Inspect `error` in each output JSONL line; successful responses have
  `error: null`.
- If long calls fail due to file size, set `VLLM_MAX_AUDIO_CLIP_FILESIZE_MB`
  or pre-split.
- If long calls produce lower quality around chunk boundaries, pre-segment by
  silence and include segment IDs in `custom_id`.
- If HTTP URL fetching fails in Docker, confirm whether the container can reach
  the host URL with the chosen networking mode.
- Keep `--allowed-media-domains` set in shared environments; the batch runner
  otherwise has no domain allowlist when the option is omitted.
- If you need named-entity prompting, compare the dedicated transcription
  endpoint against chat-completions audio on a small labeled set. Pick the path
  empirically rather than assuming `hotwords` is honored equally by every model.

## Minimal Output Parsing

Each output line looks like:

```json
{"id":"vllm-...","custom_id":"MSFT-Q1-2026-en","response":{"status_code":200,"request_id":"vllm-batch-...","body":{"text":"..."}},"error":null}
```

Extract `custom_id` and text:

```bash
jq -r '[.custom_id, (.response.body.text // ""), (.error // "")] | @tsv' \
  batch-asr/out/results.jsonl > transcripts.tsv
```

For ASR evaluation, keep a stable mapping from `custom_id` to reference
transcript path, language, and original audio URL.
