from __future__ import annotations

import unittest

import torch

from ja_media_apple.asr_config import VibeVoiceVllmAsrConfig
from ja_media_apple.asr_vibevoice_vllm import _build_chat_payload


class VibeVoiceVllmAsrConfigTest(unittest.TestCase):
    def test_repetition_penalty_defaults_to_low_penalty(self) -> None:
        config = VibeVoiceVllmAsrConfig(
            vllm_base_url="http://127.0.0.1:8000",
            vllm_model="local/vibevoice",
            load_on_startup=False,
        )

        self.assertEqual(config.runtime_backend_options()["repetition_penalty"], 0.1)

    def test_backend_options_can_override_repetition_penalty(self) -> None:
        config = VibeVoiceVllmAsrConfig(
            vllm_base_url="http://127.0.0.1:8000",
            vllm_model="local/vibevoice",
            load_on_startup=False,
            repetition_penalty=0.1,
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
            repetition_penalty=0.1,
            stop=["<|endoftext|>"],
        )

        self.assertEqual(payload["repetition_penalty"], 0.1)


if __name__ == "__main__":
    unittest.main()
