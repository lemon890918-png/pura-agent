"""Tests for TavilyProvider and WebFetchTool (Phase 9+)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from pure_agent.tools import (
    TavilyProvider,
    WebFetchParams,
    WebFetchTool,
    WebSearchTool,
)
from pure_agent.tools.web_search import DuckDuckGoProvider


# ─── TavilyProvider ─────────────────────────────────────────────────


@pytest.mark.smoke
def test_tavily_provider_name() -> None:
    p = TavilyProvider(api_key="test-key")
    assert p.name == "tavily"


@pytest.mark.smoke
def test_tavily_provider_requires_key() -> None:
    # key missing
    if "TAVILY_API_KEY" in os.environ:
        del os.environ["TAVILY_API_KEY"]
    p = TavilyProvider(api_key=None)
    import asyncio

    async def go():
        return await p.search("test")

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        asyncio.run(go())


@pytest.mark.smoke
def test_tavily_provider_search_parses_results() -> None:
    """Mock httpx to simulate a Tavily API response."""
    p = TavilyProvider(api_key="test-key")
    mock_response = {
        "results": [
            {
                "title": "Test Result",
                "url": "https://example.com",
                "content": "Snippet text",
            },
            {
                "title": "Second",
                "url": "https://example.org",
                "content": "More content",
            },
        ]
    }
    import asyncio
    from unittest.mock import MagicMock

    async def go():
        # mock the client
        mock_client = MagicMock()
        mock_post = AsyncMock()
        mock_post.raise_for_status = MagicMock()
        mock_post.json = MagicMock(return_value=mock_response)
        mock_client.post = AsyncMock(return_value=mock_post)
        p._client = mock_client
        return await p.search("test", max_results=2)

    results = asyncio.run(go())
    assert len(results) == 2
    assert results[0]["title"] == "Test Result"
    assert results[0]["url"] == "https://example.com"
    assert results[0]["snippet"] == "Snippet text"


# ─── WebSearchTool integration ──────────────────────────────────────


@pytest.mark.smoke
def test_web_search_tool_uses_tavily_when_key_set() -> None:
    """When TAVILY_API_KEY is set, tavily is in providers."""
    os.environ["TAVILY_API_KEY"] = "fake-tavily-key"
    tool = WebSearchTool()
    assert "tavily" in tool._providers
    assert "ddg" in tool._providers
    # priority order: tavily first
    order = ["tavily", "brave", "ddg"]
    order = [p for p in order if p in tool._providers]
    assert order[0] == "tavily"


@pytest.mark.smoke
def test_web_search_tool_without_tavily() -> None:
    """When TAVILY_API_KEY not set, only ddg available."""
    if "TAVILY_API_KEY" in os.environ:
        del os.environ["TAVILY_API_KEY"]
    tool = WebSearchTool()
    assert "tavily" not in tool._providers
    assert "ddg" in tool._providers


@pytest.mark.smoke
def test_web_search_tool_accepts_explicit_tavily_key() -> None:
    """WebSearchTool(tavily_api_key='...') works without env."""
    if "TAVILY_API_KEY" in os.environ:
        del os.environ["TAVILY_API_KEY"]
    tool = WebSearchTool(tavily_api_key="explicit-key")
    assert "tavily" in tool._providers


# ─── WebFetchTool ────────────────────────────────────────────────────


@pytest.mark.smoke
def test_web_fetch_tool_name() -> None:
    t = WebFetchTool()
    assert t.name == "web_fetch"


@pytest.mark.smoke
def test_web_fetch_params_validation() -> None:
    p = WebFetchParams(url="https://example.com", max_chars=5000)
    assert p.url == "https://example.com"
    assert p.max_chars == 5000
    assert p.timeout_s == 30.0  # default


@pytest.mark.smoke
def test_web_fetch_strip_html_basic() -> None:
    html = (
        "<html><head><style>body{}</style></head>"
        "<body><script>alert(1)</script>"
        "<h1>Title</h1><p>Hello &amp; world</p></body></html>"
    )
    out = WebFetchTool._strip_html(html)
    assert "alert" not in out
    assert "body{}" not in out
    assert "Title" in out
    assert "Hello & world" in out


@pytest.mark.smoke
def test_web_fetch_rejects_non_http() -> None:
    import asyncio
    t = WebFetchTool()
    r = asyncio.run(t.execute("file:///etc/passwd"))
    assert not r.ok
    assert r.error_code == "bad_url"


@pytest.mark.smoke
def test_web_fetch_success_mocked() -> None:
    """Mock httpx and verify success path."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    t = WebFetchTool()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body><h1>Hello</h1><p>World</p></body></html>"
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    t._client = mock_client

    r = asyncio.run(t.execute("https://example.com"))
    assert r.ok
    assert r.data["status"] == 200
    assert "Hello" in r.data["text"]
    assert "World" in r.data["text"]
    # no tags
    assert "<h1>" not in r.data["text"]


@pytest.mark.smoke
def test_web_fetch_http_error() -> None:
    """404 returns ToolResult.fail."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    t = WebFetchTool()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    t._client = mock_client

    r = asyncio.run(t.execute("https://example.com/notfound"))
    assert not r.ok
    assert r.error_code == "http_error"


@pytest.mark.smoke
def test_default_registry_includes_web_fetch() -> None:
    """The default registry now includes web_fetch."""
    from pure_agent.tools import build_default_registry
    reg = build_default_registry()
    names = {t.name for t in reg.all()}
    assert "web_fetch" in names
    assert "web_search" in names
