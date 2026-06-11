from ja_media_apple.asr_config import (
    AppleAsrBackendConfig,
    AppleAsrConfig,
    DEFAULT_VIBEVOICE_STOP,
    DEFAULT_VIBEVOICE_SYSTEM_PROMPT,
    OpenAiAudioTranscriptionsAsrConfig,
    VibeVoiceVllmAsrConfig,
    build_asr_backend,
    build_selected_asr_backend,
    load_apple_asr_config,
    parse_apple_asr_config,
)
from ja_media_apple.vad import DEFAULT_MLX_AUDIO_VAD_MODEL, MlxAudioVadBackend


def main():
    print("ja-media (Apple environment)")


if __name__ == "__main__":
    main()


__all__ = [
    "AppleAsrBackendConfig",
    "AppleAsrConfig",
    "DEFAULT_MLX_AUDIO_VAD_MODEL",
    "DEFAULT_VIBEVOICE_STOP",
    "DEFAULT_VIBEVOICE_SYSTEM_PROMPT",
    "MlxAudioVadBackend",
    "OpenAiAudioTranscriptionsAsrConfig",
    "VibeVoiceVllmAsrConfig",
    "build_asr_backend",
    "build_selected_asr_backend",
    "load_apple_asr_config",
    "main",
    "parse_apple_asr_config",
]
