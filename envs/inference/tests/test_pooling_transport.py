"""Checks for measured loopback transport to vLLM pooling."""

import httpx

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
