from __future__ import annotations

from pydantic import BaseModel, Field


class BulkSearchRequest(BaseModel):
    """Local-only batch of AniList title searches.

    Bulk lookup deliberately omits the direct AniList fallback switch. Callers
    can retry unresolved titles through another workflow, but a large batch
    should never surprise the operator by turning into upstream GraphQL traffic.
    """

    queries: list[str] = Field(min_length=1, max_length=2_000)
    k: int = Field(default=3, ge=1, le=50)
    include_movies: bool = False
    include_ova: bool = False
    all_formats: bool = False
    extra_fields: list[str] | str | None = Field(default=None, alias="extraFields")
    force_anilist: bool | None = Field(default=None, exclude=True)
