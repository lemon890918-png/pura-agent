"""Tests for web_search tool and DuckDuckGo provider."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from pure_agent.tools import SearchCache, WebSearchTool


def run(coro):
    """Run an async coroutine in a fresh event loop.

    Avoids "no current event loop" issues on Python 3.12+ in worker threads.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def cache(tmp_path: Path) -> SearchCache:
    return SearchCache(path=tmp_path / "search.json", ttl_seconds=60)


@pytest.mark.smoke
def test_cache_miss(cache) -> None:
    v = run(cache.get("hello", 5, "ddg"))
    assert v is None


@pytest.mark.smoke
def test_cache_set_get(cache) -> None:
    payload = {"results": [{"title": "t", "url": "u", "snippet": "s"}]}
    run(cache.set("q1", 5, "ddg", payload))
    v = run(cache.get("q1", 5, "ddg"))
    assert v is not None
    assert v["results"][0]["title"] == "t"


@pytest.mark.smoke
def test_cache_ttl_expiry(cache) -> None:
    cache.ttl = 0  # immediately expire
    run(cache.set("q", 5, "ddg", {"results": []}))
    v = run(cache.get("q", 5, "ddg"))
    assert v is None


@pytest.mark.slow
def test_ddg_real_search(cache) -> None:
    """Live test: real DuckDuckGo HTML scrape. Skipped if no network."""
    import socket

    try:
        socket.create_connection(("html.duckduckgo.com", 443), timeout=3).close()
    except OSError:
        pytest.skip("no network")

    t = WebSearchTool(cache=cache)
    try:
        r = run(t.execute(query="python programming language", max_results=3))
        if not r.ok and r.error_code == "search_failed":
            pytest.skip("DDG returned no results (rate-limited or blocked)")
        assert r.ok
        assert r.data["provider_used"] == "ddg"
        assert len(r.data["results"]) > 0
        for result in r.data["results"]:
            assert "title" in result
            assert "url" in result
            assert result["url"].startswith("http")
    finally:
        run(t.aclose())


@pytest.mark.slow
def test_ddg_cache_hit_on_second_call(cache) -> None:
    """After first successful call, second call should hit cache."""
    import socket

    try:
        socket.create_connection(("html.duckduckgo.com", 443), timeout=3).close()
    except OSError:
        pytest.skip("no network")

    t = WebSearchTool(cache=cache)
    try:
        r1 = run(t.execute(query="python pytest tutorial", max_results=2))
        if not r1.ok and r1.error_code == "search_failed":
            pytest.skip("DDG returned no results")
        assert r1.ok
        assert r1.data["cached"] is False

        r2 = run(t.execute(query="python pytest tutorial", max_results=2))
        assert r2.ok
        assert r2.data["cached"] is True
    finally:
        run(t.aclose())


@pytest.mark.smoke
def test_ddg_parse_html_handles_redirector() -> None:
    """Unit test for the HTML parser."""
    from pure_agent.tools.web_search import DuckDuckGoProvider

    sample = """
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffoo&amp;...">Foo Title</a>
    <a class="result__snippet">Foo snippet text</a>
    """
    parsed = DuckDuckGoProvider._parse_html(sample, 5)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "Foo Title"
    assert parsed[0]["url"] == "https://example.com/foo"
    assert "snippet" in parsed[0]


@pytest.mark.smoke
def test_ddg_parse_html_multiple_results() -> None:
    from pure_agent.tools.web_search import DuckDuckGoProvider

    sample = """
    <a class="result__a" href="https://a.com">A</a>
    <a class="result__snippet">sa</a>
    <a class="result__a" href="https://b.com">B</a>
    <a class="result__snippet">sb</a>
    """
    parsed = DuckDuckGoProvider._parse_html(sample, 5)
    assert len(parsed) == 2
    assert parsed[0]["url"] == "https://a.com"
    assert parsed[1]["url"] == "https://b.com"


@pytest.mark.smoke
def test_web_search_with_custom_provider() -> None:
    """End-to-end with a fake provider (no real network)."""

    class FakeProvider:
        name = "fake"

        async def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
            return [
                {"title": f"Result for {query}", "url": "https://x.com", "snippet": "fake"}
            ]

        async def aclose(self) -> None:
            pass

    cache = SearchCache(path=__import__("pathlib").Path(f"/tmp/test-web-cache-cache-custom-{__import__('os').getpid()}.json"), ttl_seconds=60)
    t = WebSearchTool(cache=cache, providers={"fake": FakeProvider()})
    try:
        r = run(t.execute(query="anything", max_results=3, provider="fake"))
        assert r.ok
        assert r.data["provider_used"] == "fake"
        assert r.data["cached"] is False
        assert r.data["results"][0]["url"] == "https://x.com"

        # second call should hit cache
        r2 = run(t.execute(query="anything", max_results=3, provider="fake"))
        assert r2.ok
        assert r2.data["cached"] is True
    finally:
        run(t.aclose())
