"""Tests for Watchdog (Phase 4)."""

from __future__ import annotations

import asyncio

import pytest

from pure_agent.agent.watchdog import (
    WatchdogTimeout,
    progress_stalled,
    run_with_timeout,
)


@pytest.mark.smoke
def test_run_with_timeout_completes() -> None:
    async def fast() -> int:
        await asyncio.sleep(0.01)
        return 42

    async def go() -> int:
        return await run_with_timeout(fast(), timeout_s=1.0)

    result = asyncio.new_event_loop().run_until_complete(go())
    assert result == 42


@pytest.mark.smoke
def test_run_with_timeout_raises_on_timeout() -> None:
    async def slow() -> int:
        await asyncio.sleep(10.0)
        return 42

    async def go() -> None:
        await run_with_timeout(slow(), timeout_s=0.1, scope="slow_op")

    with pytest.raises(WatchdogTimeout, match="slow_op"):
        asyncio.new_event_loop().run_until_complete(go())


@pytest.mark.smoke
def test_run_with_timeout_propagates_exceptions() -> None:
    async def boom() -> None:
        raise ValueError("nope")

    async def go() -> None:
        await run_with_timeout(boom(), timeout_s=1.0)

    with pytest.raises(ValueError, match="nope"):
        asyncio.new_event_loop().run_until_complete(go())


@pytest.mark.smoke
def test_progress_stalled() -> None:
    assert progress_stalled(consecutive_no_progress=4) is False
    assert progress_stalled(consecutive_no_progress=5) is True
    assert progress_stalled(consecutive_no_progress=10, threshold=3) is True
