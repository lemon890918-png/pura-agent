"""Watchdog: detect timeouts, dead loops, and progress stalls.

A watchdog wraps a coroutine with:
  - per-tool timeout
  - per-turn timeout
  - no-progress detection (consecutive turns with no useful output)

Phase 4 implementation is intentionally minimal — see Phase 5 Harness
for the full version.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class WatchdogTimeout(Exception):
    """Raised when a tool or turn exceeds its timeout."""

    def __init__(self, scope: str, timeout_s: float, elapsed: float) -> None:
        self.scope = scope
        self.timeout_s = timeout_s
        self.elapsed = elapsed
        super().__init__(f"{scope} timed out after {elapsed:.1f}s (limit {timeout_s}s)")


async def run_with_timeout(
    coro: Awaitable[Any],
    *,
    timeout_s: float,
    scope: str = "tool",
) -> Any:
    """Run an awaitable with a hard timeout. Raises WatchdogTimeout on timeout."""
    t0 = time.monotonic()
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError as e:
        elapsed = time.monotonic() - t0
        raise WatchdogTimeout(scope, timeout_s, elapsed) from e


def progress_stalled(
    *,
    consecutive_no_progress: int,
    threshold: int = 5,
) -> bool:
    """Returns True if the agent has stalled (N consecutive no-progress turns)."""
    return consecutive_no_progress >= threshold


__all__ = ["WatchdogTimeout", "run_with_timeout", "progress_stalled"]
