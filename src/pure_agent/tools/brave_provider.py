"""Brave Search provider (optional, requires BRAVE_API_KEY env or api_key param)."""

from __future__ import annotations

import httpx


class BraveProvider:
    name = "brave"

    def __init__(self, api_key: str, timeout_s: float = 15.0) -> None:
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def _client_(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={
                    "X-Subscription-Token": self.api_key,
                    "Accept": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        client = await self._client_()
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Brave HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        results = []
        for item in (data.get("web") or {}).get("results") or []:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("description", "")[:500],
                }
            )
        return results[:max_results]


__all__ = ["BraveProvider"]
