from __future__ import annotations

import json
from typing import Any

from fastapi.responses import Response


def bulk_jsonl_response(results: list[dict[str, Any]]) -> Response:
    """Return one newline-delimited JSON row per input bulk search query."""
    content = "".join(f"{json.dumps(item, ensure_ascii=False)}\n" for item in results)
    return Response(content, media_type="application/x-ndjson")
