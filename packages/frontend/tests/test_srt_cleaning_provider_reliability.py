from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ja_media_frontend.srt_cleaning.batch import read_jsonl, write_jsonl
from ja_media_frontend.srt_cleaning.provider_batch import run_provider_batch
from ja_media_frontend.srt_cleaning.provider_http import post_chat_completion


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
        self.text = json.dumps(body)

    def json(self) -> dict[str, Any]:
        return self._body


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses

    def post(self, _url: str, *, json: dict[str, Any]) -> FakeResponse:
        del json
        return self.responses.pop(0)

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_provider_retries_429_and_honors_retry_after() -> None:
    client = FakeClient(
        [
            FakeResponse(
                {"error": "slow down"},
                status_code=429,
                headers={"retry-after": "0.25"},
            ),
            FakeResponse({"model": "gpt-5.6-luna"}),
        ]
    )
    sleeps: list[float] = []

    row = post_chat_completion(
        client,  # type: ignore[arg-type]
        custom_id="clean:v2:test",
        provider_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        body={"model": "openai/gpt-5.6-luna"},
        max_attempts=3,
        sleep=sleeps.append,
    )

    assert row["response"]["status_code"] == 200
    assert row["request_attempts"] == 2
    assert sleeps == [0.25]


def test_provider_returns_final_429_after_retry_budget() -> None:
    client = FakeClient(
        [
            FakeResponse({"error": "busy"}, status_code=429),
            FakeResponse({"error": "still busy"}, status_code=429),
        ]
    )

    row = post_chat_completion(
        client,  # type: ignore[arg-type]
        custom_id="clean:v2:test",
        provider_name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        body={"model": "deepseek/deepseek-v4-flash-0731"},
        max_attempts=2,
        sleep=lambda _: None,
    )

    assert row["response"]["status_code"] == 429
    assert row["request_attempts"] == 2


def test_resume_retries_failed_requests_and_keeps_successes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    request_ids = ["clean:v2:first", "clean:v2:second"]
    input_path = tmp_path / "batch.jsonl"
    manifest_path = tmp_path / "manifest.jsonl"
    output_path = tmp_path / "results.jsonl"
    write_jsonl(input_path, [{"custom_id": value, "body": {}} for value in request_ids])
    manifests = [
        {
            "custom_id": value,
            "pipeline_version": "clean:v2",
            "anilist_id": 101,
            "subtitle_id": value,
            "repo_path": f"{value}.srt",
            "source_sha256": "a" * 64,
            "window_number": 1,
            "active_indexes": [1],
            "active_texts": ["台詞"],
        }
        for value in request_ids
    ]
    write_jsonl(manifest_path, manifests)

    def successful_row(custom_id: str) -> dict[str, Any]:
        return {
            "custom_id": custom_id,
            "response": {
                "status_code": 200,
                "body": {
                    "model": "gpt-5.6-luna",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "decisions": [
                                            {
                                                "id": 1,
                                                "decision": "as_is",
                                                "text": None,
                                                "reasons": [],
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ],
                },
            },
        }

    write_jsonl(
        output_path,
        [
            successful_row(request_ids[0]),
            {
                "custom_id": request_ids[1],
                "error": {
                    "error_kind": "validation_retry_exhausted",
                    "message": "still invalid",
                    "attempts": [],
                },
            },
        ],
    )
    called: list[str] = []

    def fake_execute(_client: Any, row: dict[str, Any], **_kwargs: Any):
        called.append(str(row["custom_id"]))
        return successful_row(str(row["custom_id"]))

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "ja_media_frontend.srt_cleaning.provider_batch.httpx.Client",
        lambda **_kwargs: FakeClient([]),
    )
    monkeypatch.setattr(
        "ja_media_frontend.srt_cleaning.provider_batch.execute_request", fake_execute
    )
    monkeypatch.setattr(
        "ja_media_frontend.srt_cleaning.provider_batch.reconstruct_provider_output",
        lambda **_kwargs: tmp_path / "reconstruct",
    )
    args = argparse.Namespace(
        anilist=None,
        workspace_root=None,
        run_id="current",
        input=str(input_path),
        manifest=str(manifest_path),
        out=str(output_path),
        provider="openai",
        model=None,
        base_url=None,
        api_key_env=None,
        response_format="auto",
        body_json=None,
        concurrency=2,
        limit=None,
        timeout=180.0,
        request_attempts=8,
        repair_attempts=1,
        resume=True,
        force=False,
    )

    run_provider_batch(args)

    assert called == [request_ids[1]]
    assert [row["custom_id"] for row in read_jsonl(output_path)] == request_ids
