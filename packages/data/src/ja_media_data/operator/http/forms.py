"""Small bounded form parser for the HTML-only operator routes."""

from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import HTTPException, Request


async def bounded_form(request: Request) -> dict[str, str]:
    payload = await request.body()
    if len(payload) > 16 * 1024:
        raise HTTPException(status_code=413, detail="form is too large")
    parsed = parse_qs(payload.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
    return {key: values[-1] for key, values in parsed.items()}


def optional_int(
    value: str | None,
    *,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    cleaned = value.strip() if value is not None else ""
    if not cleaned:
        return None
    try:
        parsed = int(cleaned)
    except ValueError as error:
        raise HTTPException(
            status_code=422, detail=f"{field} must be an integer"
        ) from error
    if minimum is not None and parsed < minimum:
        raise HTTPException(status_code=422, detail=f"{field} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise HTTPException(status_code=422, detail=f"{field} must be <= {maximum}")
    return parsed
