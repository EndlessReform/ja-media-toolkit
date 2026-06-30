# ja-media inference environment

This uv project owns client-side model adapters that are heavier than
`packages/core` but not inherently Apple-, CUDA-, or service-specific.

Use this environment for inference clients that may need libraries such as
`transformers`, `tokenizers`, `huggingface_hub`, `nagisa`, Torch, or audio ML
helpers while still talking to a separate compute-plane service.

## Qwen3 forced alignment smoke

The Qwen3 forced-aligner server runs in `deploy/qwen3-forced-aligner-vllm`.
From this directory:

```sh
uv sync
uv run qwen3-align-smoke --base-url http://melchior-1:8000
```

The smoke reads the fixture in
`../../examples/forced-alignment/qwen3-tts/`, segments the untimed source text
with nagisa, sends word-level timestamp markers to vLLM `/pooling`, and merges
the word timings back into source-line groups. The same grouping layer can be
fed from subtitle cues for SRT/ASS retiming.
