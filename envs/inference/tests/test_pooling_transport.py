"""Checks for measured loopback transport to vLLM pooling."""

import json

import httpx
import numpy as np

from ja_media_inference.forced_alignment import pooling_transport


def test_profiled_transport_counts_wire_bytes_and_stage_times(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"data": [[0.1, 0.9]]}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(pooling_transport.httpx, "Client", lambda **_kwargs: client)

    result, profile = pooling_transport.post_pooling_profiled(
        "http://vllm/pooling",
        {"model": "test", "messages": [], "task": "token_classify"},
        timeout_s=1,
    )

    assert result["data"][0]["data"] == [[0.1, 0.9]]
    assert profile["vllm_request_bytes"] > 0
    assert profile["vllm_response_bytes"] > 0
    assert profile["vllm_request_to_headers_s"] >= 0
    assert profile["vllm_response_body_s"] >= 0
    assert profile["vllm_response_json_s"] >= 0


def test_profiled_binary_transport_decodes_fp16_matrix(monkeypatch) -> None:
    expected = np.array([[0.1, 0.9], [0.8, 0.2]], dtype="<f2")

    def handler(request: httpx.Request) -> httpx.Response:
        metadata = {
            "data": [
                {
                    "index": 0,
                    "embed_dtype": "float16",
                    "endianness": "little",
                    "start": 0,
                    "end": expected.nbytes,
                    "shape": list(expected.shape),
                }
            ]
        }
        return httpx.Response(
            200,
            content=expected.tobytes(),
            headers={"metadata": json.dumps(metadata)},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(pooling_transport.httpx, "Client", lambda **_kwargs: client)

    result, profile = pooling_transport.post_pooling_binary_profiled(
        "http://vllm/pooling",
        {"model": "test", "messages": [], "task": "token_classify"},
        timeout_s=1,
    )

    np.testing.assert_array_equal(result, expected)
    assert profile["vllm_response_bytes"] == expected.nbytes
    assert profile["vllm_response_decode_s"] >= 0
