# Add Client Code

Add the smallest client that matches the target project. Read
`ANIME_CROSSWALK_BASE_URL` from environment/config and reject missing values
with a clear error.

## Python Client

Use this when the target project is Python and does not already depend on
`ja_media_core`.

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class CrosswalkLookup:
    source: str
    id: str
    media_kind: str | None
    count: int
    results: list[dict[str, Any]]


@dataclass(frozen=True)
class CrosswalkBulkLookup:
    count: int
    results: list[CrosswalkLookup]


class AnimeCrosswalkClient:
    def __init__(self, base_url: str | None = None, timeout: float = 10.0) -> None:
        base = base_url or os.environ.get("ANIME_CROSSWALK_BASE_URL")
        if not base:
            raise RuntimeError("ANIME_CROSSWALK_BASE_URL is not set")
        self.base_url = base.rstrip("/")
        self.timeout = timeout

    def _get_json(self, path: str) -> dict[str, Any]:
        request = Request(f"{self.base_url}{path}", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"anime crosswalk HTTP {exc.code}: {detail}") from exc

    def resolve(
        self,
        source: str,
        external_id: str | int,
        media_kind: str | None = None,
    ) -> CrosswalkLookup:
        source_part = quote(str(source), safe="")
        id_part = quote(str(external_id), safe="")
        if media_kind:
            kind_part = quote(str(media_kind), safe="")
            payload = self._get_json(f"/resolve/{source_part}/{kind_part}/{id_part}")
        else:
            payload = self._get_json(f"/resolve/{source_part}/{id_part}")
        return CrosswalkLookup(
            source=payload["source"],
            id=payload["id"],
            media_kind=payload.get("media_kind"),
            count=int(payload["count"]),
            results=list(payload["results"]),
        )

    def resolve_many(self, lookups: list[dict[str, Any]]) -> CrosswalkBulkLookup:
        payload = json.dumps({"lookups": lookups}).encode("utf-8")
        request = Request(
            f"{self.base_url}/resolve/bulk",
            data=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"anime crosswalk HTTP {exc.code}: {detail}") from exc
        return CrosswalkBulkLookup(
            count=int(data["count"]),
            results=[
                CrosswalkLookup(
                    source=item["source"],
                    id=item["id"],
                    media_kind=item.get("media_kind"),
                    count=int(item["count"]),
                    results=list(item["results"]),
                )
                for item in data["results"]
            ],
        )

    def tvdb(self, tvdb_id: str | int, media_kind: str | None = None) -> CrosswalkLookup:
        return self.resolve("tvdb", tvdb_id, media_kind)

    def mal(self, mal_id: str | int) -> CrosswalkLookup:
        return self.resolve("mal", mal_id)

    def anilist(self, anilist_id: str | int) -> CrosswalkLookup:
        return self.resolve("anilist", anilist_id)

    def tmdb(self, tmdb_id: str | int, media_kind: str) -> CrosswalkLookup:
        return self.resolve("tmdb", tmdb_id, media_kind)


client = AnimeCrosswalkClient()
lookup = client.tvdb(79099, media_kind="movie")
anilist_ids = [row["anilist_id"] for row in lookup.results if row.get("anilist_id")]
bulk = client.resolve_many([
    {"source": "tvdb", "id": 79099, "media_kind": "movie"},
    {"source": "mal", "id": 3269},
])
```

## TypeScript Client

Use this when the target project is TypeScript/JavaScript and already has
`fetch`.

```ts
export type CrosswalkLookup = {
  source: string;
  id: string;
  media_kind: string | null;
  count: number;
  results: Array<Record<string, unknown>>;
};

export class AnimeCrosswalkClient {
  readonly baseUrl: string;

  constructor(baseUrl = process.env.ANIME_CROSSWALK_BASE_URL) {
    if (!baseUrl) throw new Error("ANIME_CROSSWALK_BASE_URL is not set");
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async resolve(source: string, id: string | number, mediaKind?: string): Promise<CrosswalkLookup> {
    const parts = ["resolve", source, mediaKind, String(id)]
      .filter((part): part is string => Boolean(part))
      .map(encodeURIComponent);
    const response = await fetch(`${this.baseUrl}/${parts.join("/")}`, {
      headers: {Accept: "application/json"},
    });
    if (!response.ok) {
      throw new Error(`anime crosswalk HTTP ${response.status}: ${await response.text()}`);
    }
    return (await response.json()) as CrosswalkLookup;
  }

  async resolveMany(
    lookups: Array<{source: string; id: string | number; media_kind?: "tv" | "movie"}>,
  ): Promise<{count: number; results: CrosswalkLookup[]}> {
    const response = await fetch(`${this.baseUrl}/resolve/bulk`, {
      method: "POST",
      headers: {Accept: "application/json", "Content-Type": "application/json"},
      body: JSON.stringify({lookups}),
    });
    if (!response.ok) {
      throw new Error(`anime crosswalk HTTP ${response.status}: ${await response.text()}`);
    }
    return (await response.json()) as {count: number; results: CrosswalkLookup[]};
  }

  tvdb(id: string | number, mediaKind?: "tv" | "movie") {
    return this.resolve("tvdb", id, mediaKind);
  }

  mal(id: string | number) {
    return this.resolve("mal", id);
  }

  anilist(id: string | number) {
    return this.resolve("anilist", id);
  }

  tmdb(id: string | number, mediaKind: "tv" | "movie") {
    return this.resolve("tmdb", id, mediaKind);
  }
}
```

## Integration Checklist

- Add configuration docs for `ANIME_CROSSWALK_BASE_URL`.
- Add one no-match test: response `count: 0` should not throw.
- Add one multiple-match test: caller should not silently take the first result
  unless that is a deliberate product decision.
- If using bulk lookup, add one mixed hit/no-match test and assert response
  order matches request order.
- Keep network errors separate from no-match results.
- If the target project already has HTTP retry/backoff conventions, use them.
