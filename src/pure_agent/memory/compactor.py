"""Auto-compactor: when prompt tokens exceed threshold, summarize middle messages.

Strategy:
  1. Keep system_prompt (always)
  2. Keep last 2 turns raw (most-recent context)
  3. Summarize middle messages via LLM (terse bullets, factoids)
  4. Replace middle turns with one summary message

Limits:
  - max compactions per run: 3 (further compactions raise)
  - summary targets: ~30% of original token count

Phase 3 implementation:
  - The Compactor takes a ProviderAdapter (so it can call the LLM)
  - It receives a list[CanonicalMessage] and threshold
  - Returns a new list[CanonicalMessage]
"""

from __future__ import annotations

from dataclasses import dataclass

from pure_agent.model import (
    CanonicalMessage,
    Role,
    TextBlock,
)
from pure_agent.model.canonical import Usage
from pure_agent.model.provider import ProviderAdapter
from pure_agent.model.token_counter import estimate_tokens


_PROMPT = """You are a context compactor for a coding agent. Your job is to take
a sequence of chat messages and produce a terse bullet-list summary that
preserves the essential facts and decisions.

Rules:
- Use 5-15 bullets maximum.
- Each bullet: one fact, one line, terse.
- Preserve file paths, function names, error messages.
- Drop pleasantries, retries, redundant info.
- Group by intent (read / edit / result) when possible.
- Output ONLY the bullet list, no preamble, no commentary."""


@dataclass
class CompactionResult:
    """Result of a single compaction."""

    new_messages: list[CanonicalMessage]
    summary_text: str
    original_tokens: int
    compacted_tokens: int
    usage: Usage


class Compactor:
    """Compacts messages via LLM summarization."""

    def __init__(self, provider: ProviderAdapter, model: str, *, max_compactions: int = 3) -> None:
        self.provider = provider
        self.model = model
        self.max_compactions = max_compactions
        self._call_count = 0

    def reset(self) -> None:
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    async def compact(
        self,
        messages: list[CanonicalMessage],
        *,
        keep_last: int = 4,
    ) -> CompactionResult:
        """Compact a message list.

        Args:
          messages: full conversation
          keep_last: number of trailing messages to keep raw (default 4 = 2 turns)

        Returns: CompactionResult with new messages + summary
        """
        if len(messages) <= keep_last + 1:
            return CompactionResult(
                new_messages=list(messages),
                summary_text="",
                original_tokens=0,
                compacted_tokens=sum(estimate_tokens(m.text()) for m in messages),
                usage=Usage(),
            )
        if self._call_count >= self.max_compactions:
            raise RuntimeError(
                f"compaction limit reached ({self.max_compactions}); further "
                f"compactions would lose too much detail"
            )

        # split: system (first) | middle (to summarize) | tail (keep raw)
        head: list[CanonicalMessage] = []
        if messages and messages[0].role == Role.SYSTEM:
            head.append(messages[0])
        middle = messages[len(head) : len(messages) - keep_last]
        tail = messages[len(head) + len(middle) :]

        if not middle:
            return CompactionResult(
                new_messages=list(messages),
                summary_text="",
                original_tokens=0,
                compacted_tokens=sum(estimate_tokens(m.text()) for m in messages),
                usage=Usage(),
            )

        # call LLM to summarize
        middle_text = "\n\n".join(
            f"[{m.role.value}] {m.text()[:2000]}" for m in middle
        )
        summary_text, usage = await self._summarize(middle_text)
        self._call_count += 1

        original_tokens = sum(estimate_tokens(m.text()) for m in middle)
        summary_msg = CanonicalMessage(
            role=Role.SYSTEM,
            content=[
                TextBlock(
                    text=f"[Compaction #{self._call_count} — summary of {len(middle)} earlier messages]\n{summary_text}",
                )
            ],
            synthetic=True,
        )

        new_messages = head + [summary_msg] + tail
        compacted_tokens = estimate_tokens(summary_text) + sum(
            estimate_tokens(m.text()) for m in head + tail
        )

        return CompactionResult(
            new_messages=new_messages,
            summary_text=summary_text,
            original_tokens=original_tokens,
            compacted_tokens=compacted_tokens,
            usage=usage,
        )

    async def _summarize(self, middle_text: str) -> tuple[str, Usage]:
        from pure_agent.model import CanonicalRequest, ModelEvent

        req = CanonicalRequest(
            model=self.model,
            messages=[
                CanonicalMessage.from_text(Role.SYSTEM, _PROMPT),
                CanonicalMessage.from_text(Role.USER, middle_text),
            ],
            tools=[],
            max_output_tokens=2048,
            temperature=0.0,
        )
        text_parts: list[str] = []
        usage = Usage()
        async for ev in self.provider.stream(req):
            if ev.type == "text_delta" and ev.text:
                text_parts.append(ev.text)
            elif ev.type == "usage" and ev.usage:
                usage = ev.usage
        return "".join(text_parts).strip(), usage


__all__ = ["Compactor", "CompactionResult"]
