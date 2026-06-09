"""ContextBuilder: assemble memory layers into a system prompt section.

Phase 6. Reads L1 (in-memory) / L2 (episodic) / L3 (semantic) / L4 (procedural)
and formats them as system-prompt-ready text with token budget allocation.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pure_agent.memory.l1_short import L1Cache

@dataclass
class ContextBudget:
    """Token allocation across memory layers."""

    l1: int = 200
    l2: int = 1000
    l3: int = 500
    l4: int = 300
    total_cap: int = 2000

    def total(self) -> int:
        return self.l1 + self.l2 + self.l3 + self.l4


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to roughly fit within max_tokens (4 chars/token heuristic)."""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


class ContextBuilder:
    """Builds a system-prompt section from 4 memory layers.

    Caching: results are cached for `cache_ttl_s` seconds to avoid recomputing
    on every turn.
    """

    def __init__(
        self,
        *,
        budget: ContextBudget | None = None,
        cache_ttl_s: float = 60.0,
        l1: L1Cache | None = None,
        l2_getter: Callable[[], list[dict[str, Any]]] | None = None,
        l3_getter: Callable[[], list[dict[str, Any]]] | None = None,
        l4_getter: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.budget = budget or ContextBudget()
        self.cache_ttl_s = cache_ttl_s
        self.l1 = l1 or L1Cache()
        self._l2_getter = l2_getter or (lambda: [])
        self._l3_getter = l3_getter or (lambda: [])
        self._l4_getter = l4_getter or (lambda: [])
        self._cache: tuple[float, str] | None = None  # (timestamp, rendered)

    def invalidate(self) -> None:
        self._cache = None

    def build(self) -> str:
        """Render the memory section as markdown."""
        # cache check
        if self._cache is not None:
            ts, text = self._cache
            if time.monotonic() - ts < self.cache_ttl_s:
                return text

        sections: list[str] = []
        # L4 first (user prefs are most important)
        l4_items = self._l4_getter()
        if l4_items:
            text = self._render_l4(l4_items)
            if text:
                sections.append(f"## User Preferences\n{text}")

        # L3 (project facts)
        l3_items = self._l3_getter()
        if l3_items:
            text = self._render_l3(l3_items)
            if text:
                sections.append(f"## Project Facts\n{text}")

        # L2 (episodic)
        l2_items = self._l2_getter()
        if l2_items:
            text = self._render_l2(l2_items)
            if text:
                sections.append(f"## Session Context\n{text}")

        # L1 (in-memory cache)
        l1_items = list(self.l1)
        if l1_items:
            text = self._render_l1(l1_items)
            if text:
                sections.append(f"## Recent Items\n{text}")

        rendered = "\n\n".join(sections) if sections else ""
        # enforce total budget
        if _estimate_tokens(rendered) > self.budget.total_cap:
            rendered = _truncate_to_tokens(rendered, self.budget.total_cap)
        self._cache = (time.monotonic(), rendered)
        return rendered

    def _render_l4(self, items: list[dict[str, Any]]) -> str:
        lines = []
        budget = self.budget.l4
        for it in items:
            text = it.get("text") or it.get("key") or str(it)
            line = f"- {text}"
            lines.append(line)
        return _truncate_to_tokens("\n".join(lines), budget)

    def _render_l3(self, items: list[dict[str, Any]]) -> str:
        lines = []
        budget = self.budget.l3
        for it in items:
            text = it.get("text") or it.get("fact") or str(it)
            lines.append(f"- {text}")
        return _truncate_to_tokens("\n".join(lines), budget)

    def _render_l2(self, items: list[dict[str, Any]]) -> str:
        lines = []
        budget = self.budget.l2
        for it in items:
            text = it.get("text") or it.get("content") or str(it)
            lines.append(f"- {text}")
        return _truncate_to_tokens("\n".join(lines), budget)

    def _render_l1(self, items: list[tuple[str, Any]]) -> str:
        lines = []
        budget = self.budget.l1
        for k, v in items:
            if isinstance(v, str):
                lines.append(f"- {k}: {v}")
            else:
                lines.append(f"- {k}: {str(v)[:80]}")
        return _truncate_to_tokens("\n".join(lines), budget)


__all__ = ["ContextBuilder", "ContextBudget"]
