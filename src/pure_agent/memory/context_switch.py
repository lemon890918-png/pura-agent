"""ContextSwitcher: switch between sessions while preserving memory.

Phase 6. Each session has:
  - L1 (ShortTermMemory) — saved on switch, restored on switch back
  - L2 (episodic)        — kept per session
  - L3 (semantic)        — shared across sessions in same project
  - L4 (procedural)      — shared globally
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pure_agent.memory.l1_short import L1Cache


@dataclass
class SessionSnapshot:
    """Saved L1 + metadata for a session."""

    session_id: str
    l1_snapshot: dict[str, Any]
    saved_at: float = field(default_factory=time.monotonic)
    ttl_s: float = 600.0  # 10 min to switch back

    def is_expired(self) -> bool:
        return time.monotonic() - self.saved_at > self.ttl_s


class ContextSwitcher:
    """Manages session switching.

    - Current session: has a live L1
    - On switch: save current L1, swap in target session's L1 (or fresh)
    - History: last N snapshots, with TTL
    """

    def __init__(
        self,
        *,
        current_session_id: str,
        l1_factory: Callable[[], L1Cache] | None = None,
        max_history: int = 20,
        snapshot_ttl_s: float = 600.0,
    ) -> None:
        self._current_id = current_session_id
        self._l1_factory = l1_factory or L1Cache
        self._live_l1: dict[str, L1Cache] = {
            current_session_id: self._l1_factory()
        }
        self._snapshots: OrderedDict[str, SessionSnapshot] = OrderedDict()
        self.max_history = max_history
        self.snapshot_ttl_s = snapshot_ttl_s
        # history stack of session switches
        self._history: list[str] = [current_session_id]

    @property
    def current_session_id(self) -> str:
        return self._current_id

    @property
    def current_l1(self) -> L1Cache:
        return self._live_l1[self._current_id]

    def switch(self, new_session_id: str) -> L1Cache:
        """Switch to a different session, returning the new live L1."""
        if new_session_id == self._current_id:
            return self.current_l1
        # save current snapshot
        cur = self._live_l1[self._current_id]
        snap = SessionSnapshot(
            session_id=self._current_id,
            l1_snapshot=cur.snapshot(),
            ttl_s=self.snapshot_ttl_s,
        )
        self._snapshots[self._current_id] = snap
        # pop oldest if over cap
        while len(self._snapshots) > self.max_history:
            self._snapshots.popitem(last=False)
        # switch
        self._current_id = new_session_id
        if new_session_id in self._live_l1:
            # restore
            l1 = self._live_l1[new_session_id]
        elif new_session_id in self._snapshots:
            # restore from snapshot
            l1 = self._l1_factory()
            l1.restore(self._snapshots[new_session_id].l1_snapshot)
            self._live_l1[new_session_id] = l1
        else:
            # fresh session
            l1 = self._l1_factory()
            self._live_l1[new_session_id] = l1
        # update history
        self._history.append(new_session_id)
        if len(self._history) > 100:
            self._history = self._history[-100:]
        return l1

    def history(self) -> list[str]:
        return list(self._history)

    def evict_expired(self) -> int:
        """Remove expired snapshots. Returns count removed."""
        expired = [k for k, v in self._snapshots.items() if v.is_expired()]
        for k in expired:
            del self._snapshots[k]
        return len(expired)

    def snapshot_count(self) -> int:
        return len(self._snapshots)

    def live_session_count(self) -> int:
        return len(self._live_l1)


__all__ = ["ContextSwitcher", "SessionSnapshot"]
