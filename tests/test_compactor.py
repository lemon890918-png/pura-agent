"""Tests for Compactor with mock provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from pure_agent.model import (
    CanonicalMessage,
    CanonicalRequest,
    ModelEvent,
    ProviderAdapter,
    Role,
    TextBlock,
    Usage,
)
from pure_agent.memory import Compactor


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeProvider:
    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.calls = 0

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        yield ModelEvent(type="message_start")
        yield ModelEvent(type="text_delta", text=self.summary)
        yield ModelEvent(type="usage", usage=Usage(prompt_tokens=100, completion_tokens=20, total_tokens=120))
        yield ModelEvent(type="message_end")

    def normalize_tool_schema(self, s):
        return {}

    def max_context_tokens(self, m=None):
        return 128_000


@pytest.mark.smoke
def test_compact_short_returns_as_is() -> None:
    provider = FakeProvider("summary")
    c = Compactor(provider, "mock")
    msgs = [
        CanonicalMessage.from_text(Role.SYSTEM, "system"),
        CanonicalMessage.from_text(Role.USER, "hi"),
    ]
    result = run(c.compact(msgs, keep_last=4))
    assert len(result.new_messages) == 2
    assert provider.calls == 0


@pytest.mark.smoke
def test_compact_long_keeps_head_and_tail() -> None:
    provider = FakeProvider("• did thing A\n• did thing B")
    c = Compactor(provider, "mock")
    msgs = [
        CanonicalMessage.from_text(Role.SYSTEM, "system prompt"),
        CanonicalMessage.from_text(Role.USER, "u1"),
        CanonicalMessage.from_text(Role.ASSISTANT, "a1"),
        CanonicalMessage.from_text(Role.USER, "u2"),
        CanonicalMessage.from_text(Role.ASSISTANT, "a2"),
        CanonicalMessage.from_text(Role.USER, "u3 (recent)"),
        CanonicalMessage.from_text(Role.ASSISTANT, "a3 (recent)"),
    ]
    result = run(c.compact(msgs, keep_last=4))
    # should be: system + summary + 4 raw tail
    assert len(result.new_messages) == 1 + 1 + 4
    # head preserved
    assert result.new_messages[0].role == Role.SYSTEM
    assert result.new_messages[0].text() == "system prompt"
    # summary in middle
    summary_msg = result.new_messages[1]
    assert "Compaction" in summary_msg.text()
    assert "• did thing A" in summary_msg.text()
    # tail preserved
    assert result.new_messages[-1].text() == "a3 (recent)"
    assert provider.calls == 1


@pytest.mark.smoke
def test_compact_max_compactions_enforced() -> None:
    provider = FakeProvider("summary")
    c = Compactor(provider, "mock", max_compactions=2)
    long_msgs = (
        [CanonicalMessage.from_text(Role.SYSTEM, "sys")]
        + [CanonicalMessage.from_text(Role.USER, f"u{i}") for i in range(20)]
        + [CanonicalMessage.from_text(Role.ASSISTANT, f"a{i}") for i in range(20)]
    )
    run(c.compact(long_msgs, keep_last=4))
    run(c.compact(long_msgs, keep_last=4))
    with pytest.raises(RuntimeError, match="compaction limit"):
        run(c.compact(long_msgs, keep_last=4))


@pytest.mark.smoke
def test_compact_count_accurate() -> None:
    provider = FakeProvider("• bullet 1")
    c = Compactor(provider, "mock")
    assert c.call_count == 0
    long_msgs = [CanonicalMessage.from_text(Role.SYSTEM, "sys")] + [
        CanonicalMessage.from_text(Role.USER, f"u{i}") for i in range(20)
    ]
    run(c.compact(long_msgs, keep_last=4))
    assert c.call_count == 1
    c.reset()
    assert c.call_count == 0
