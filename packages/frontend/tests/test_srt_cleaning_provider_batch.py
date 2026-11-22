from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ja_media_frontend.srt_cleaning.batch import (
    build_batch_row,
    build_manifest_row,
    build_windows,
)
from ja_media_frontend.srt_cleaning.contracts import SourceDocument
from ja_media_frontend.srt_cleaning.execution_manifest import write_execution_manifest
from ja_media_frontend.srt_cleaning.provider_batch import (
    execute_request,
    prepare_body,
    resolve_provider,
)


SRT_TEXT = """1
00:00:01,000 --> 00:00:02,000
一

2
00:00:02,000 --> 00:00:03,000
二
"""


class FakeResponse:
    def __init__(
        self,
        body: dict[str, Any],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(body, ensure_ascii=False)

    def json(self) -> dict[str, Any]:
        return self._body


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.bodies: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any]) -> FakeResponse:
        assert url.endswith("/chat/completions")
        self.bodies.append(json)
        return self.responses.pop(0)


def completion(decisions: list[dict[str, Any]], model: str) -> FakeResponse:
    return FakeResponse(
        {
            "model": model,
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"decisions": decisions}, ensure_ascii=False
                        )
                    }
                }
            ],
        }
    )


def request_fixture(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = tmp_path / "source.srt"
    source_path.write_text(SRT_TEXT, encoding="utf-8")
    source = SourceDocument(
        anilist_id=101,
        subtitle_id="sub-one",
        repo_path="Group/source.srt",
        filename="source.srt",
        source_path=source_path,
    )
    window = build_windows(
        source,
        SRT_TEXT,
        window_size=2,
        context_cues=0,
        prompt_policy_sha256="a" * 64,
    )[0]
    return (
        build_batch_row(
            window,
            model="generated-model",
            policy_text="policy",
            series_context="AniList ID: 101",
        ),
        build_manifest_row(window, model="generated-model"),
    )


def provider_args(provider: str) -> argparse.Namespace:
    return argparse.Namespace(
        provider=provider,
        model=None,
        base_url=None,
        api_key_env=None,
        response_format="auto",
    )


def test_provider_presets_select_requested_models() -> None:
    openai = resolve_provider(provider_args("openai"))
    deepseek = resolve_provider(provider_args("deepseek"))

    assert (openai.model, openai.response_format) == ("gpt-5.6-luna", "keep")
    assert (deepseek.model, deepseek.response_format) == (
        "deepseek-v4-flash",
        "json-object",
    )


def test_deepseek_body_uses_json_object_mode(tmp_path: Path) -> None:
    request, _ = request_fixture(tmp_path)
    provider = resolve_provider(provider_args("deepseek"))

    body = prepare_body(request["body"], provider, {"thinking": {"type": "disabled"}})

    assert body["model"] == "deepseek-v4-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "disabled"}


def test_invalid_result_gets_one_stateless_repair(tmp_path: Path) -> None:
    request, manifest = request_fixture(tmp_path)
    invalid = completion(
        [
            {"id": 1, "decision": "edit", "text": "一", "reasons": []},
            {"id": 2, "decision": "as_is", "text": None, "reasons": []},
        ],
        "gpt-5.6-luna",
    )
    valid = completion(
        [
            {"id": 1, "decision": "as_is", "text": None, "reasons": []},
            {
                "id": 2,
                "decision": "edit",
                "text": "二。",
                "reasons": ["punctuation"],
            },
        ],
        "gpt-5.6-luna",
    )
    client = FakeClient([invalid, valid])
    provider = resolve_provider(provider_args("openai"))

    row = execute_request(
        client,  # type: ignore[arg-type]
        request,
        manifests={request["custom_id"]: manifest},
        provider=provider,
        body_override={},
        repair_attempts=1,
    )

    assert row["response"]["body"]["model"] == "gpt-5.6-luna"
    assert len(row["repair_failures"]) == 1
    assert len(client.bodies) == 2
    assert "failed validation" in client.bodies[1]["messages"][-1]["content"]


def test_second_invalid_result_goes_to_failed_window_output(tmp_path: Path) -> None:
    request, manifest = request_fixture(tmp_path)
    invalid = completion(
        [{"id": 1, "decision": "as_is", "text": None, "reasons": []}],
        "deepseek-v4-flash",
    )
    client = FakeClient([invalid, invalid])
    provider = resolve_provider(provider_args("deepseek"))

    row = execute_request(
        client,  # type: ignore[arg-type]
        request,
        manifests={request["custom_id"]: manifest},
        provider=provider,
        body_override={},
        repair_attempts=1,
    )

    assert row["error"]["error_kind"] == "validation_retry_exhausted"
    assert row["error"]["retryable"] is False
    assert len(row["error"]["attempts"]) == 2


def test_execution_manifest_records_served_model(tmp_path: Path) -> None:
    output_path = tmp_path / "results.jsonl"
    path = write_execution_manifest(
        output_path,
        provider="openai",
        requested_model="gpt-5.6-luna",
        rows=[{"response": {"body": {"model": "gpt-5.6-luna-2026-08-01"}}}],
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["requested_model"] == "gpt-5.6-luna"
    assert payload["served_models"] == ["gpt-5.6-luna-2026-08-01"]
