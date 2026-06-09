"""Steer queue: user-side injections that arrive during a running agent.

A SteerQueue is an asyncio.Queue of CanonicalMessage. The agent loop
drains it at the start of each turn — if a user typed something
mid-execution, that message is appended to the conversation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

from pure_agent.model import CanonicalMessage, Role, TextBlock


class SteerQueue:
    """FIFO queue of user-injected messages.

    Used for:
      - REPL: user types while agent runs
      - Plan: user edits a step mid-flight
      - Watchdog: an automatic message is injected on stall
    """

    def __init__(self) -> None:
        self._q: asyncio.Queue[CanonicalMessage] = asyncio.Queue()

    async def put(self, message: CanonicalMessage) -> None:
        await self._q.put(message)

    def put_nowait(self, message: CanonicalMessage) -> None:
        self._q.put_nowait(message)

    async def put_text(self, text: str) -> None:
        """Convenience: wrap text in a USER role message."""
        await self._q.put(CanonicalMessage.from_text(Role.USER, text, synthetic=True))

    def put_text_nowait(self, text: str) -> None:
        self._q.put_nowait(
            CanonicalMessage.from_text(Role.USER, text, synthetic=True)
        )

    def drain(self) -> list[CanonicalMessage]:
        """Drain all pending messages (non-blocking)."""
        out: list[CanonicalMessage] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    @property
    def qsize(self) -> int:
        return self._q.qsize()

    def __len__(self) -> int:
        return self._q.qsize()


__all__ = ["SteerQueue"]
