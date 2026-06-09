"""Session manager: in-memory per-session state with lock.

Phase 7. Each session holds:
  - id, title, created_at, last_used_at
  - messages (list of CanonicalMessage)
  - lock (asyncio.Lock) to prevent concurrent runs
  - steer_queue, abort_event, checkpointer
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pure_agent.agent import SteerQueue
from pure_agent.memory import ContextBuilder, MemoryLayers
from pure_agent.model import CanonicalMessage
from pure_agent.persistence import Database


@dataclass
class SessionState:
    """Live state of one session."""

    id: str
    title: str
    created_at: float
    last_used_at: float
    db: Database
    memory: MemoryLayers
    context: ContextBuilder
    messages: list[CanonicalMessage] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    steer_queue: SteerQueue = field(default_factory=SteerQueue)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)

    def touch(self) -> None:
        self.last_used_at = time.time()

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "n_messages": len(self.messages),
        }


class SessionManager:
    """In-process session manager (Phase 7)."""

    def __init__(self, db: Database, *, project_id: str = "default") -> None:
        self.db = db
        self.project_id = project_id
        self._sessions: dict[str, SessionState] = {}
        self._ensure_default_project()

    def _ensure_default_project(self) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.db.conn.execute(
            "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.project_id, "Default", self.project_id, now, now),
        )

    def _new_id(self) -> str:
        return f"ses_{uuid.uuid4().hex[:12]}"

    def create(self, title: str = "untitled") -> SessionState:
        sid = self._new_id()
        now = time.time()
        memory = MemoryLayers(self.db, session_id=sid, project_id=self.project_id)

        # Build context with current memory
        def l2_getter():
            return [{"text": f.text} for f in memory.episodic.recent(20)]

        def l3_getter():
            return [{"text": f["fact"]} for f in memory.semantic.search("", limit=20)]

        def l4_getter():
            return [{"text": memory.procedural.as_prompt_section()}]

        context = ContextBuilder(
            l2_getter=l2_getter,
            l3_getter=l3_getter,
            l4_getter=l4_getter,
        )

        state = SessionState(
            id=sid,
            title=title,
            created_at=now,
            last_used_at=now,
            db=self.db,
            memory=memory,
            context=context,
        )
        self._sessions[sid] = state
        return state

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str) -> SessionState:
        s = self.get(session_id)
        if s is not None:
            s.touch()
            return s
        # create with a stable id (not auto-generated) so the caller's id is honored
        from datetime import datetime, timezone
        now = time.time()
        memory = MemoryLayers(self.db, session_id=session_id, project_id=self.project_id)

        def l2_getter():
            return [{"text": f.text} for f in memory.episodic.recent(20)]

        def l3_getter():
            return [{"text": f["fact"]} for f in memory.semantic.search("", limit=20)]

        def l4_getter():
            return [{"text": memory.procedural.as_prompt_section()}]

        context = ContextBuilder(
            l2_getter=l2_getter,
            l3_getter=l3_getter,
            l4_getter=l4_getter,
        )
        state = SessionState(
            id=session_id,
            title=session_id,
            created_at=now,
            last_used_at=now,
            db=self.db,
            memory=memory,
            context=context,
        )
        self._sessions[session_id] = state
        return state

    def list(self) -> list[SessionState]:
        return list(self._sessions.values())

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def count(self) -> int:
        return len(self._sessions)

    # ─── Phase 9: persistence ────────────────────────────────────────

    def persist(self) -> int:
        """Write session metadata to sessions table. Returns count."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        n = 0
        for s in self._sessions.values():
            self.db.conn.execute(
                "INSERT OR REPLACE INTO sessions "
                "(id, project_id, name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (s.id, self.project_id, s.title,
                 datetime.fromtimestamp(s.created_at, timezone.utc).isoformat(),
                 now),
            )
            n += 1
        return n

    def load_persisted(self) -> int:
        """Reload sessions from sessions table into in-memory state.

        L1 (in-memory) is not recovered; that's by design.
        Returns count of loaded sessions.
        """
        from datetime import datetime, timezone
        rows = self.db.conn.execute(
            "SELECT * FROM sessions WHERE project_id = ?",
            (self.project_id,),
        ).fetchall()
        n = 0
        for r in rows:
            sid = r["id"]
            if sid in self._sessions:
                continue  # already in memory
            # reconstruct
            try:
                created_at = datetime.fromisoformat(r["created_at"]).timestamp()
            except (ValueError, TypeError):
                created_at = time.time()
            now = time.time()
            memory = MemoryLayers(self.db, session_id=sid, project_id=self.project_id)

            def l2_getter(sid=sid, mem=memory):
                return [{"text": f.text} for f in mem.episodic.recent(20)]

            def l3_getter(mem=memory):
                return [{"text": f["fact"]} for f in mem.semantic.search("", limit=20)]

            def l4_getter(mem=memory):
                return [{"text": mem.procedural.as_prompt_section()}]

            context = ContextBuilder(
                l2_getter=l2_getter,
                l3_getter=l3_getter,
                l4_getter=l4_getter,
            )
            state = SessionState(
                id=sid,
                title=r["name"],
                created_at=created_at,
                last_used_at=now,
                db=self.db,
                memory=memory,
                context=context,
            )
            self._sessions[sid] = state
            n += 1
        return n


__all__ = ["SessionManager", "SessionState"]
