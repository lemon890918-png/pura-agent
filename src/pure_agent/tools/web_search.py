"""Web search tool with multi-provider fallback and 24h cache.

Phase 1: DuckDuckGo HTML scraping (no key needed) + JSON file cache.
Phase 2+: add Brave / Tavily as optional providers when keys are configured.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from pure_agent.config import get_home
from pure_agent.tools.base import Tool, ToolResult


# ─── cache ────────────────────────────────────────────────────────────────────


class SearchCache:
    """JSON file cache, 24h TTL."""

    def __init__(self, path: Path | None = None, ttl_seconds: int = 24 * 3600) -> None:
        if path is None:
            path = get_home() / "cache" / "web_search.json"
        self.path = path
        self.ttl = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _key(self, query: str, max_results: int, provider: str) -> str:
        return hashlib.sha256(
            f"{provider}|{query}|{max_results}".encode("utf-8")
        ).hexdigest()[:24]

    async def get(self, query: str, max_results: int, provider: str) -> dict | None:
        k = self._key(query, max_results, provider)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return None
        entry = data.get(k)
        if not entry:
            return None
        if time.time() - entry.get("ts", 0) > self.ttl:
            return None
        return entry.get("payload")

    async def set(self, query: str, max_results: int, provider: str, payload: dict) -> None:
        k = self._key(query, max_results, provider)
        async with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            except (OSError, json.JSONDecodeError):
                data = {}
            data[k] = {"ts": time.time(), "payload": payload}
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
            os.replace(tmp, self.path)


# ─── DDG provider (HTML scrape) ────────────────────────────────────────────────


class TavilyProvider:
    """Tavily search API. Requires TAVILY_API_KEY env var.

    Docs: https://docs.tavily.com/docs/rest-api/api-reference
    Endpoint: POST https://api.tavily.com/search
    """
    name = "tavily"

    def __init__(self, api_key: str | None = None, timeout_s: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY", "")
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    def _ensure_key(self) -> None:
        if not self.api_key:
            raise ValueError(
                "TAVILY_API_KEY not set. Pass api_key= or set TAVILY_API_KEY env."
            )

    async def _client_(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[dict[str, Any]]:
        self._ensure_key()
        client = await self._client_()
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        r = await client.post("https://api.tavily.com/search", json=payload)
        r.raise_for_status()
        data = r.json()
        results: list[dict[str, Any]] = []
        for item in data.get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                }
            )
        return results


class DuckDuckGoProvider:
    """DuckDuckGo HTML scrape. No API key needed.

    Tries the lite endpoint first; falls back to HTML scraping.
    """

    name = "ddg"
    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    def __init__(self, timeout_s: float = 15.0) -> None:
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    async def _client_(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Return list of {title, url, snippet}."""
        client = await self._client_()
        url = "https://html.duckduckgo.com/html/"
        try:
            resp = await client.post(url, data={"q": query, "kl": "us-en"})
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            raise RuntimeError(f"DDG request failed: {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(
                f"DDG returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        results = self._parse_html(resp.text, max_results)
        if not results:
            raise RuntimeError(
                f"DDG returned 0 results (likely anti-bot; body len={len(resp.text)})"
            )
        return results

    @staticmethod
    def _parse_html(html: str, max_results: int) -> list[dict[str, str]]:
        # DDG HTML results are inside <a class="result__a" href="...">title</a>
        # and <a class="result__snippet">snippet</a>.
        # We use a simple regex parser; the structure is stable enough.
        results: list[dict[str, str]] = []
        # result__a  pattern
        a_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snip_pattern = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        # DDG uses //duckduckgo.com/l/?uddg=<encoded> as a redirector
        for m in a_pattern.finditer(html):
            raw_url = m.group(1)
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            # unwrap DDG redirect
            if "uddg=" in raw_url:
                mm = re.search(r"uddg=([^&]+)", raw_url)
                if mm:
                    from urllib.parse import unquote

                    raw_url = unquote(mm.group(1))
            results.append({"title": title, "url": raw_url, "snippet": ""})
        # fill snippets
        snippets = snip_pattern.findall(html)
        for i, s in enumerate(snippets):
            if i < len(results):
                results[i]["snippet"] = re.sub(r"<[^>]+>", "", s).strip()[:500]
        return results[:max_results]


# ─── tool ─────────────────────────────────────────────────────────────────────


class WebSearchParams(BaseModel):
    query: str = Field(..., min_length=1, description="Search query text")
    max_results: int = Field(5, ge=1, le=20, description="Max results to return")
    provider: str | None = Field(
        None,
        description="Force provider (tavily / brave / ddg). Default: auto-detect from env (tavily>brave>ddg).",
    )


class WebSearchTool(Tool):
    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "Search the web. Returns a list of {title, url, snippet} results. "
        "Provider priority: tavily (best) > brave > ddg (fallback). "
        "Set provider='tavily' | 'brave' | 'ddg' to force a specific one. "
        "If TAVILY_API_KEY is set, Tavily is used automatically (best for AI agents). "
        "24h cache. provider='tavily' returns clean AI-ready results."
    )
    parameters: ClassVar[dict] = WebSearchParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = WebSearchParams

    # simple rate limiter (1 req per second across all calls)
    _RATE_LOCK = asyncio.Lock()
    _LAST_REQ_AT: float = 0.0
    _RATE_INTERVAL = 1.0  # seconds

    def __init__(
        self,
        cache: SearchCache | None = None,
        providers: dict[str, Any] | None = None,
        brave_api_key: str | None = None,
        tavily_api_key: str | None = None,
    ) -> None:
        self.cache = cache or SearchCache()
        # provider registry (priority order: tavily > brave > ddg)
        self._providers: dict[str, Any] = {}
        ddg = DuckDuckGoProvider()
        self._providers["ddg"] = ddg
        if brave_api_key:
            from pure_agent.tools.brave_provider import BraveProvider

            self._providers["brave"] = BraveProvider(brave_api_key)
        if tavily_api_key or os.environ.get("TAVILY_API_KEY"):
            self._providers["tavily"] = TavilyProvider(tavily_api_key)
        if providers:
            self._providers.update(providers)

    async def aclose(self) -> None:
        for p in self._providers.values():
            if hasattr(p, "aclose"):
                await p.aclose()

    async def _rate_limit(self) -> None:
        async with self._RATE_LOCK:
            now = time.time()
            wait = self._RATE_INTERVAL - (now - self._LAST_REQ_AT)
            if wait > 0:
                await asyncio.sleep(wait)
            self.__class__._LAST_REQ_AT = time.time()

    async def execute(
        self,
        query: str,
        max_results: int = 5,
        provider: str | None = None,
    ) -> ToolResult:
        # choose provider (priority: tavily > brave > ddg)
        order = ["tavily", "brave", "ddg"] if provider is None else [provider]
        order = [p for p in order if p in self._providers]
        if not order:
            return ToolResult.fail(f"no provider available (requested: {provider})", code="no_provider")

        for prov in order:
            cached = await self.cache.get(query, max_results, prov)
            if cached is not None:
                return ToolResult.ok_data(
                    {
                        "results": cached.get("results", []),
                        "provider_used": prov,
                        "cached": True,
                    }
                )

            await self._rate_limit()
            try:
                p = self._providers[prov]
                results = await p.search(query, max_results)
                if not results:
                    continue  # try next
                payload = {"results": results, "provider_used": prov, "cached": False}
                await self.cache.set(query, max_results, prov, payload)
                return ToolResult.ok_data(payload)
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue

        return ToolResult.fail(
            f"all providers failed; last error: {last_err if 'last_err' in locals() else 'no result'}",
            code="search_failed",
        )


__all__ = ["WebSearchTool", "SearchCache", "DuckDuckGoProvider", "TavilyProvider"]
