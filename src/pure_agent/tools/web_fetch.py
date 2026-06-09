"""Web fetch tool: download a URL and return text content.

Phase 9+ feature. Used after web_search to get full page content.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from pure_agent.tools.base import Tool, ToolResult


class WebFetchParams(BaseModel):
    url: str = Field(..., description="The URL to fetch (must be http or https)")
    max_chars: int = Field(
        default=20000, description="Maximum characters of body to return"
    )
    timeout_s: float = Field(default=30.0, description="Request timeout in seconds")


class WebFetchTool(Tool):
    """Download a URL and return the text content (HTML stripped)."""

    name: ClassVar[str] = "web_fetch"
    description: ClassVar[str] = (
        "Download a URL and return its text content. Use this AFTER web_search "
        "to get the full content of a page. Supports http and https. Returns "
        "up to max_chars characters of text (HTML tags stripped)."
    )
    parameters: ClassVar[dict] = WebFetchParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = WebFetchParams

    user_agent: ClassVar[str] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    def __init__(self, cache_dir: Path | None = None, timeout_s: float = 30.0) -> None:
        self.timeout_s = timeout_s
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = cache_dir
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

    async def execute(
        self,
        url: str,
        max_chars: int = 20000,
        timeout_s: float = 30.0,
    ) -> ToolResult:
        if not url.startswith(("http://", "https://")):
            return ToolResult.fail(
                f"unsupported URL scheme: {url!r} (only http/https)",
                code="bad_url",
            )
        client = await self._client_()
        try:
            r = await client.get(url, timeout=timeout_s)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            return ToolResult.fail(f"fetch failed: {e}", code="fetch_failed")
        if r.status_code != 200:
            return ToolResult.fail(
                f"HTTP {r.status_code} for {url}",
                code="http_error",
            )
        text = self._strip_html(r.text)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[truncated to {max_chars} chars]"
        return ToolResult.ok_data(
            {
                "url": url,
                "status": r.status_code,
                "text": text,
                "length": len(text),
            }
        )

    @staticmethod
    def _strip_html(html: str) -> str:
        # remove script and style blocks
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # remove tags
        text = re.sub(r"<[^>]+>", " ", html)
        # decode common entities
        text = (
            text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
        )
        # collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()


__all__ = ["WebFetchTool", "WebFetchParams"]
