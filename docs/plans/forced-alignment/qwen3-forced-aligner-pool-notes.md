# Qwen3 Forced Aligner `/pooling` Notes

These notes summarize the current plan for using
`Qwen/Qwen3-ForcedAligner-0.6B` through vLLM's `/pooling` endpoint.

## Timestamp Placement

- The application/consumer chooses the alignment units and inserts
  `<timestamp>` markers.
- vLLM does not segment reference text into words, morphemes, or other spans.
  Use whitespace, spaCy, nagisa, MeCab, regex, or any other span logic before
  constructing the prompt.
- BPE tokenization still happens inside each chosen span, but timestamp
  prediction is attached to explicit `<timestamp>` token positions, not to BPE
  word boundaries.
- The example prompt format is:

  ```text
  <|audio_start|><|audio_pad|><|audio_end|>word1<timestamp><timestamp>word2<timestamp><timestamp>
  ```

- Two timestamp predictions are consumed per span: start and end.

## What The Client Needs To Know

- The client needs to know its own chosen spans and the prompt suffix it
  constructed from those spans.
- The client needs `timestamp_token_id` and `timestamp_segment_time` from the
  model config.
- The client does not need to know the audio encoder sample rate, hop size, or
  audio feature-token expansion formula when using the right-aligned extraction
  strategy below.
- The server owns audio decoding, feature extraction, audio placeholder
  expansion, and audio encoder execution.
- The `/pooling` response returns raw token-classification logits, not expanded
  prompt token IDs or structured `{word, start, end}` alignments.
- Because the server expands `<|audio_pad|>` into many audio tokens before the
  text/timestamp suffix, the client should avoid left-index assumptions. With a
  raw chat template that appends no suffix, map local timestamp positions to
  server logits from the right:

  ```python
  local_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
  predictions = logits.argmax(dim=-1)

  for i, token_id in enumerate(local_ids):
      if token_id != timestamp_token_id:
          continue
      server_i = len(predictions) - (len(local_ids) - i)
      timestamp_ms = predictions[server_i].item() * timestamp_segment_time
  ```

- This works because the unknown audio expansion is to the left of the
  timestamp-bearing suffix.

## Server Startup

- Use vLLM's pooling runner and override the architecture:

  ```bash
  vllm serve Qwen/Qwen3-ForcedAligner-0.6B \
    --runner pooling \
    --enforce-eager \
    --chat-template /path/to/raw_content_chat_template.jinja \
    --hf-overrides '{"architectures": ["Qwen3ASRForcedAlignerForTokenClassification"]}'
  ```

- The raw chat template should render exactly the request content, for example:

  ```jinja
  {{ messages[0]['content'] }}
  ```

- Using a server startup `--chat-template` avoids requiring the client to send a
  trusted per-request template.

## Client API Shape

- Call `/pooling`, not `/v1/chat/completions`.
- Send one user message containing the forced-alignment prompt and audio URL.
- Set `"task": "token_classify"`.

Example payload:

```json
{
  "model": "Qwen/Qwen3-ForcedAligner-0.6B",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "<|audio_start|><|audio_pad|><|audio_end|>Hello<timestamp><timestamp>world<timestamp><timestamp>"
        },
        {
          "type": "audio_url",
          "audio_url": {
            "url": "data:audio/wav;base64,..."
          }
        }
      ]
    }
  ],
  "task": "token_classify"
}
```

Response shape is generic pooling output:

```json
{
  "data": [
    {
      "index": 0,
      "object": "pooling",
      "data": [[0.0, 0.1], [0.2, 0.3]]
    }
  ],
  "usage": {
    "prompt_tokens": 123,
    "total_tokens": 123
  }
}
```

The client converts `data[0].data` to logits, argmaxes per token, extracts the
right-aligned `<timestamp>` rows, multiplies by `timestamp_segment_time`, and
pairs every two timestamps with the corresponding input span.

## Extracting Timestamp Rows

- The `/pooling` response does not say which output rows are `<timestamp>`
  rows. It only returns a logits matrix.
- Each row in `data[0].data` corresponds to one token position in the server's
  final expanded prompt.
- The client finds the timestamp rows by tokenizing the exact prompt suffix it
  built, finding local positions whose token ID is `timestamp_token_id`, and
  mapping those local positions to server output rows.
- The upstream online example does this in
  `examples/pooling/token_classify/forced_alignment_online.py`, in `main`:
  it loads the tokenizer/config, converts response `data` to logits, argmaxes
  rows, tokenizes the prompt locally, finds `timestamp_token_id`, and maps local
  indices to prediction indices.
- The upstream example uses a left-shift calculation:

  ```python
  audio_token_shift = len(predictions) - len(token_ids)
  prediction_index = i + audio_token_shift if i > audio_pad_index else i
  ```

- With a server-owned raw chat template and a prompt that ends with the
  timestamp-bearing text suffix, prefer right-aligned extraction so the client
  does not need to know the audio expansion length:

  ```python
  import torch
  from transformers import AutoConfig, AutoTokenizer


  def extract_span_times_ms(
      *,
      model_name: str,
      prompt: str,
      pooling_json: dict,
      num_spans: int,
  ) -> list[tuple[float, float]]:
      tokenizer = AutoTokenizer.from_pretrained(model_name)
      config = AutoConfig.from_pretrained(model_name)

      timestamp_token_id = config.timestamp_token_id
      timestamp_segment_time = config.timestamp_segment_time

      logits = torch.tensor(pooling_json["data"][0]["data"])
      predictions = logits.argmax(dim=-1)

      local_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]

      timestamp_ms = []
      for local_i, token_id in enumerate(local_ids):
          if token_id != timestamp_token_id:
              continue

          # Map local prompt suffix coordinates to server output coordinates.
          # This assumes all unknown expansion is to the left of this suffix.
          server_i = len(predictions) - (len(local_ids) - local_i)
          timestamp_ms.append(
              predictions[server_i].item() * timestamp_segment_time
          )

      expected = num_spans * 2
      if len(timestamp_ms) != expected:
          raise RuntimeError(
              f"Expected {expected} timestamp predictions, got "
              f"{len(timestamp_ms)}."
          )

      return [
          (timestamp_ms[i * 2], timestamp_ms[i * 2 + 1])
          for i in range(num_spans)
      ]
  ```

- `num_spans` is the number of application-chosen alignment units used to build
  the prompt. Pair the returned `(start_ms, end_ms)` values with those same
  spans in order.
- The extraction token is `timestamp_token_id`, not the printed string
  `"<timestamp>"`. The string is only how the prompt is written before
  tokenization.

## Code Anchors

- Forced-aligner model head:
  `vllm/model_executor/models/qwen3_asr_forced_aligner.py`
  - `Qwen3ASRForcedAlignerForTokenClassification`
  - `forward`
- Qwen3-ASR multimodal processing:
  `vllm/model_executor/models/qwen3_asr.py`
  - `Qwen3ASRMultiModalProcessor._get_prompt_updates`
  - `Qwen3ASRForConditionalGeneration._process_audio_input`
- Qwen3-ASR HF processor wrapper:
  `vllm/transformers_utils/processors/qwen3_asr.py`
  - `Qwen3ASRProcessor.__call__`
  - `Qwen3ASRProcessor.replace_multimodal_special_tokens`
- Offline forced-alignment example:
  `examples/pooling/token_classify/forced_alignment_offline.py`
  - `build_prompt`
  - timestamp extraction from `output.prompt_token_ids`
- Online forced-alignment example:
  `examples/pooling/token_classify/forced_alignment_online.py`
  - server startup command
  - `build_prompt`
  - `/pooling` payload with `"task": "token_classify"`
  - client-side timestamp extraction
- Pooling request/response API:
  `vllm/entrypoints/pooling/pooling/protocol.py`
  - `PoolingChatRequest`
  - `PoolingResponseData`
  - `PoolingResponse`
- Pooling serving response serialization:
  `vllm/entrypoints/pooling/pooling/serving.py`
  - `ServingPooling._verify_pooling_task`
  - `ServingPooling.request_output_to_pooling_json_response`
- Pooling output encoding:
  `vllm/entrypoints/pooling/utils.py`
  - `encode_pooling_output_float`
  - `get_pooling_output_encoder`
- Pooling task detection:
  `vllm/config/model.py`
  - `ModelConfig.get_pooling_task`
