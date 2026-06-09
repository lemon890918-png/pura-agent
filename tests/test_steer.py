"""Tests for SteerQueue (Phase 4)."""

from __future__ import annotations

import asyncio

import pytest

from pure_agent.agent import SteerQueue
from pure_agent.model import CanonicalMessage, Role, TextBlock


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.smoke
def test_steer_queue_put_drain() -> None:
    q = SteerQueue()
    q.put_text_nowait("hello")
    q.put_text_nowait("world")
    msgs = q.drain()
    assert len(msgs) == 2
    assert msgs[0].role == Role.USER
    assert msgs[0].text() == "hello"
    assert msgs[1].text() == "world"
    # second drain → empty
    assert q.drain() == []


@pytest.mark.smoke
def test_steer_queue_drain_empty() -> None:
    q = SteerQueue()
    assert q.drain() == []


@pytest.mark.smoke
def test_steer_queue_qsize() -> None:
    q = SteerQueue()
    assert q.qsize == 0
    q.put_text_nowait("a")
    q.put_text_nowait("b")
    assert q.qsize == 2
    q.drain()
    assert q.qsize == 0


@pytest.mark.smoke
def test_steer_queue_async_put() -> None:
    q = SteerQueue()

    async def go() -> list[CanonicalMessage]:
        await q.put_text("from coroutine")
        return q.drain()

    msgs = run(go())
    assert len(msgs) == 1
    assert "from coroutine" in msgs[0].text()


@pytest.mark.smoke
def test_steer_queue_accepts_canonical_message() -> None:
    q = SteerQueue()
    m = CanonicalMessage(role=Role.ASSISTANT, content=[TextBlock(text="hi")])
    q.put_nowait(m)
    drained = q.drain()
    assert len(drained) == 1
    assert drained[0].text() == "hi"
