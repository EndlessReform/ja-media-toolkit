from __future__ import annotations

import unittest

import httpx
import torch

from ja_media_apple.asr_config import VibeVoiceVllmAsrConfig
from ja_media_apple.asr_vibevoice_vllm import (
    _build_chat_payload,
    _post_to_vllm_async,
)


class VibeVoiceVllmAsrConfigTest(unittest.TestCase):
    def test_repetition_penalty_defaults_to_mild_penalty(self) -> None:
        config = VibeVoiceVllmAsrConfig(
            vllm_base_url="http://127.0.0.1:8000",
            vllm_model="local/vibevoice",
            load_on_startup=False,
        )

        self.assertEqual(config.runtime_backend_options()["repetition_penalty"], 1.1)
        self.assertEqual(
            config.runtime_backend_options()["vllm_request_max_attempts"],
            3,
        )
        self.assertEqual(
            config.runtime_backend_options()["vllm_request_retry_backoff_s"],
            1.0,
        )

    def test_backend_options_can_override_repetition_penalty(self) -> None:
        config = VibeVoiceVllmAsrConfig(
            vllm_base_url="http://127.0.0.1:8000",
            vllm_model="local/vibevoice",
            load_on_startup=False,
            repetition_penalty=1.1,
            backend_options={"repetition_penalty": 1.05},
        )

        self.assertEqual(config.runtime_backend_options()["repetition_penalty"], 1.05)


class VibeVoiceVllmPayloadTest(unittest.TestCase):
    def test_chat_payload_includes_repetition_penalty(self) -> None:
        payload = _build_chat_payload(
            model="local/vibevoice",
            system_prompt="transcribe",
            user_text="audio please",
            audio_rows=torch.zeros((1, 2)),
            max_tokens=128,
            temperature=0.0,
            top_p=1.0,
            repetition_penalty=1.1,
            stop=["<|endoftext|>"],
        )

        self.assertEqual(payload["repetition_penalty"], 1.1)


class VibeVoiceVllmPostTest(unittest.IsolatedAsyncioTestCase):
    async def test_post_retries_transient_read_error(self) -> None:
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadError("connection dropped", request=request)
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response_json, _elapsed_s = await _post_to_vllm_async(
                client,
                "http://vllm.test",
                {"model": "local/vibevoice"},
                max_attempts=2,
                retry_backoff_s=0.0,
            )

        self.assertEqual(response_json, {"ok": True})
        self.assertEqual(calls, 2)

    async def test_post_does_not_retry_client_error(self) -> None:
        calls = 0

        async def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(400, text="bad payload")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(RuntimeError, "HTTP 400: bad payload"):
                await _post_to_vllm_async(
                    client,
                    "http://vllm.test",
                    {"model": "local/vibevoice"},
                    max_attempts=3,
                    retry_backoff_s=0.0,
                )

        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
