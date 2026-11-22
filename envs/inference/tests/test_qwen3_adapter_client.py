"""Checks for the compact Mac-to-adapter HTTP client."""

import httpx

from ja_media_inference.forced_alignment.qwen3_adapter_client import (
    Qwen3AdapterClient,
)
from ja_media_inference.forced_alignment.text_units import AlignmentToken


def test_compact_client_preserves_caller_tokens_and_score_metadata() -> None:
    token = AlignmentToken(id="token:1", text="台詞", group_id="cue:1", group_index=0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/audio/cache":
            return httpx.Response(200, json={"audio_id": "a" * 64})
        return httpx.Response(
            200,
            json={
                "profile": {
                    "adapter_decode_s": 0.1,
                    "vllm_request_to_headers_s": 0.2,
                },
                "alignments": [
                    {
                        "token_id": "token:1",
                        "start_s": 1.2,
                        "end_s": 1.8,
                        "confidence": 0.6,
                        "metadata": {"normalized_entropy": 0.2},
                    }
                ],
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = Qwen3AdapterClient(base_url="http://adapter", client=http)

    audio_id = client.cache_audio(
        {
            "object_bucket": "test",
            "object_key": "key",
            "sha256": "a" * 64,
            "codec": "ac3",
        }
    )
    results = client.align_crop(
        audio_id=audio_id, crop_start_s=10, crop_end_s=70, tokens=[token]
    )

    assert results[0].token is token
    assert results[0].start_s == 1.2
    assert results[0].metadata == {"normalized_entropy": 0.2}

    profiled = client.align_crop_profiled(
        audio_id=audio_id, crop_start_s=10, crop_end_s=70, tokens=[token]
    )
    assert profiled.profile["adapter_decode_s"] == 0.1
    assert profiled.profile["vllm_request_to_headers_s"] == 0.2
    assert profiled.profile["adapter_http_to_headers_s"] >= 0
    assert profiled.profile["adapter_http_body_s"] >= 0
    assert profiled.profile["adapter_http_json_s"] >= 0
    assert profiled.profile["adapter_response_bytes"] > 0
