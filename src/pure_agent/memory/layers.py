"""4-layer memory CRUD on top of the existing SQLite tables.

Each layer is a thin facade over the Phase-0 schema. They share the same
Database instance, no extra migrations needed (Phase 0 already created
memory_short / memory_episodic / memory_semantic / memory_procedural).

Phase 3 adds the *active* usage:
  - read relevant facts
  - write new facts (with TTL / dedup / weight)
  - purge expired
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pure_agent.persistence.db import Database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "mem") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ─── L1 short-term (in-context) ────────────────────────────────────────────


@dataclass
class ShortTermMemory:
    """L1: in-context facts. Kept in the messages themselves, not stored.

    Short-term is just `messages` in the AIAgentLoop — we don't persist it
    between steps. This class is a thin wrapper for tests / inspector use.
    """

    session_id: str
    items: list[dict[str, Any]] = field(default_factory=list)

    def add(self, kind: str, content: Any) -> str:
        item_id = _new_id("st")
        self.items.append({"id": item_id, "kind": kind, "content": content})
        return item_id

    def clear(self) -> None:
        self.items.clear()

    def as_prompt_section(self) -> str:
        if not self.items:
            return ""
        lines = ["[Short-term context]"]
        for it in self.items:
            lines.append(f"- ({it['kind']}) {it['content']}")
        return "\n".join(lines)


# ─── L2 episodic (per session) ─────────────────────────────────────────────


@dataclass
class EpisodicFact:
    event: str
    content_json: str
    importance: float = 0.5
    id: str = field(default_factory=lambda: _new_id("ep"))


class EpisodicMemory:
    """L2: per-session episodic facts. E.g. 'user asked to add multiply function'."""

    def __init__(self, db: Database, session_id: str) -> None:
        self.db = db
        # ensure the session exists (FK to sessions table)
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc).isoformat()
        db.conn.execute(
            "INSERT OR IGNORE INTO sessions (id, project_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, "default", session_id, now, now),
        )
        self.session_id = session_id

    def add(self, event: str, content: Any, importance: float = 0.5) -> str:
        f = EpisodicFact(
            event=event,
            content_json=json.dumps(content, ensure_ascii=False),
            importance=importance,
        )
        self.db.conn.execute(
            "INSERT INTO memory_episodic (id, session_id, event, importance, content_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f.id, self.session_id, f.event, f.importance, f.content_json, _now_iso()),
        )
        return f.id

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            "SELECT * FROM memory_episodic WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (self.session_id, limit),
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "event": r["event"],
                    "importance": r["importance"],
                    "content": json.loads(r["content_json"]),
                    "created_at": r["created_at"],
                }
            )
        return out

    def as_prompt_section(self, limit: int = 10) -> str:
        facts = self.recent(limit=limit)
        if not facts:
            return ""
        lines = ["[Recent events in this session]"]
        for f in facts:
            summary = json.dumps(f["content"], ensure_ascii=False)
            if len(summary) > 200:
                summary = summary[:200] + "…"
            lines.append(f"- ({f['event']}) {summary}")
        return "\n".join(lines)

    def purge_older_than(self, days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = self.db.conn.execute(
            "DELETE FROM memory_episodic WHERE session_id = ? AND created_at < ?",
            (self.session_id, cutoff),
        )
        return cur.rowcount


# ─── L3 semantic (per project) ─────────────────────────────────────────────


@dataclass
class SemanticFact:
    fact: str
    source: str | None = None
    confidence: float = 1.0
    id: str = field(default_factory=lambda: _new_id("sm"))


class SemanticMemory:
    """L3: per-project semantic facts. E.g. 'project uses Python 3.12 + uv'."""

    def __init__(self, db: Database, project_id: str) -> None:
        self.db = db
        # ensure the project exists (FK to projects)
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc).isoformat()
        db.conn.execute(
            "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, project_id, project_id, now, now),
        )
        self.project_id = project_id

    def add(self, fact: str, source: str | None = None, confidence: float = 1.0) -> str:
        f = SemanticFact(fact=fact, source=source, confidence=confidence)
        now = _now_iso()
        self.db.conn.execute(
            "INSERT INTO memory_semantic (id, project_id, fact, source, confidence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f.id, self.project_id, f.fact, f.source, f.confidence, now, now),
        )
        # also add to FTS
        self.db.conn.execute(
            "INSERT INTO memory_semantic_fts (memory_id, fact) VALUES (?, ?)",
            (f.id, f.fact),
        )
        return f.id

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        # naive: LIKE search (FTS5 is set up but not required for Phase 3)
        rows = self.db.conn.execute(
            "SELECT * FROM memory_semantic WHERE project_id = ? AND fact LIKE ? "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (self.project_id, f"%{query}%", limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "fact": r["fact"],
                "source": r["source"],
                "confidence": r["confidence"],
            }
            for r in rows
        ]

    def all_facts(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            "SELECT * FROM memory_semantic WHERE project_id = ? "
            "ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (self.project_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "fact": r["fact"],
                "source": r["source"],
                "confidence": r["confidence"],
            }
            for r in rows
        ]

    def as_prompt_section(self, limit: int = 20) -> str:
        facts = self.all_facts(limit=limit)
        if not facts:
            return ""
        lines = ["[Project facts]"]
        for f in facts:
            conf = f["confidence"]
            prefix = "✓" if conf >= 0.8 else "~" if conf >= 0.5 else "?"
            lines.append(f"  {prefix} {f['fact']}")
        return "\n".join(lines)


# ─── L4 procedural (per user) ──────────────────────────────────────────────


@dataclass
class UserPref:
    kind: str  # language, tool, style, etc.
    content: str
    weight: float = 1.0
    id: str = field(default_factory=lambda: _new_id("up"))


class ProceduralMemory:
    """L4: per-user preferences. E.g. 'user prefers Chinese' / 'uses typer'."""

    def __init__(self, db: Database, user_id: str = "default") -> None:
        self.db = db
        # no FK on memory_user_prefs; nothing to ensure
        self.user_id = user_id

    def add(self, kind: str, content: str, weight: float = 1.0) -> str:
        p = UserPref(kind=kind, content=content, weight=weight)
        now = _now_iso()
        self.db.conn.execute(
            "INSERT INTO memory_user_prefs (id, user_id, kind, content, weight, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (p.id, self.user_id, p.kind, p.content, p.weight, now, now),
        )
        return p.id

    def all_prefs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            "SELECT * FROM memory_user_prefs WHERE user_id = ? "
            "ORDER BY weight DESC, updated_at DESC LIMIT ?",
            (self.user_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "content": r["content"],
                "weight": r["weight"],
            }
            for r in rows
        ]

    def as_prompt_section(self, limit: int = 20) -> str:
        prefs = self.all_prefs(limit=limit)
        if not prefs:
            return ""
        lines = ["[User preferences]"]
        for p in prefs:
            lines.append(f"- ({p['kind']}) {p['content']}")
        return "\n".join(lines)


# ─── combined facade ───────────────────────────────────────────────────────


@dataclass
class MemoryLayers:
    """Convenience bundle of all 4 layers."""

    db: Database
    session_id: str
    project_id: str
    user_id: str = "default"

    @property
    def short(self) -> ShortTermMemory:
        return ShortTermMemory(self.session_id)

    @property
    def episodic(self) -> EpisodicMemory:
        return EpisodicMemory(self.db, self.session_id)

    @property
    def semantic(self) -> SemanticMemory:
        return SemanticMemory(self.db, self.project_id)

    @property
    def procedural(self) -> ProceduralMemory:
        return ProceduralMemory(self.db, self.user_id)

    def as_prompt_sections(self) -> str:
        """Concatenate all 4 layers into one prompt section."""
        parts = [
            self.procedural.as_prompt_section(),
            self.semantic.as_prompt_section(),
            self.episodic.as_prompt_section(),
        ]
        return "\n\n".join(p for p in parts if p)


__all__ = [
    "ShortTermMemory",
    "EpisodicMemory",
    "EpisodicFact",
    "SemanticMemory",
    "SemanticFact",
    "ProceduralMemory",
    "UserPref",
    "MemoryLayers",
]
