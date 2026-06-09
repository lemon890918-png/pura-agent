"""L1 short-term memory: in-memory conversation cache.

Phase 6强化: TTL + LRU + max size.
L1 不持久化，session 切换时 save 到 switch_history 还能恢复.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class L1Item:
    """A cached item in L1 short-term memory."""

    key: str
    value: Any
    created_at: float = field(default_factory=time.monotonic)
    expires_at: float | None = None
    hits: int = 0
    importance: float = 0.5

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.monotonic() > self.expires_at


class L1Cache:
    """In-memory LRU cache for short-term context.

    L1 layer is not persisted to SQLite. It lives only in this process.
    Items are evicted on max size (LRU) or expired by TTL.
    """

    def __init__(
        self,
        *,
        max_size: int = 100,
        default_ttl_s: float | None = None,
    ) -> None:
        self.max_size = max_size
        self.default_ttl_s = default_ttl_s
        self._items: OrderedDict[str, L1Item] = OrderedDict()

    def put(
        self,
        key: str,
        value: Any,
        *,
        ttl_s: float | None = None,
        importance: float = 0.5,
    ) -> None:
        if key in self._items:
            self._items.move_to_end(key)
        expires = None
        if ttl_s is not None:
            expires = time.monotonic() + ttl_s
        elif self.default_ttl_s is not None:
            expires = time.monotonic() + self.default_ttl_s
        self._items[key] = L1Item(
            key=key,
            value=value,
            expires_at=expires,
            importance=importance,
        )
        # evict LRU until under max
        self._evict_lru()

    def get(self, key: str) -> Any | None:
        if key not in self._items:
            return None
        item = self._items[key]
        if item.is_expired():
            del self._items[key]
            return None
        item.hits += 1
        self._items.move_to_end(key)  # mark as recently used
        return item.value

    def pop(self, key: str) -> Any | None:
        item = self._items.pop(key, None)
        return item.value if item is not None else None

    def remove(self, key: str) -> bool:
        return self._items.pop(key, None) is not None

    def contains(self, key: str) -> bool:
        return key in self._items and not self._items[key].is_expired()

    def keys(self) -> list[str]:
        return [k for k, v in self._items.items() if not v.is_expired()]

    def recent(self, n: int = 10) -> list[tuple[str, Any]]:
        return list(self._items.items())[-n:]

    def top_by_hits(self, n: int = 10) -> list[L1Item]:
        return sorted(self._items.values(), key=lambda x: -x.hits)[:n]

    def top_by_importance(self, n: int = 10) -> list[L1Item]:
        return sorted(self._items.values(), key=lambda x: -x.importance)[:n]

    def clear(self) -> int:
        n = len(self._items)
        self._items.clear()
        return n

    def __len__(self) -> int:
        return len([v for v in self._items.values() if not v.is_expired()])

    def snapshot(self) -> dict[str, Any]:
        """Serialize for context switch handoff."""
        return {k: v.value for k, v in self._items.items() if not v.is_expired()}

    def restore(self, data: dict[str, Any]) -> None:
        """Restore from a snapshot."""
        self._items.clear()
        for k, v in data.items():
            self.put(k, v)

    def _evict_lru(self) -> None:
        while len(self._items) > self.max_size:
            # pop oldest (LRU first = first item in OrderedDict)
            self._items.popitem(last=False)

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        for k, v in self._items.items():
            if not v.is_expired():
                yield k, v.value


__all__ = ["L1Cache", "L1Item"]
