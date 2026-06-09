"""Checkpoint: persist / resume a running conversation.

Phase 4 implementation. Persists after each turn to SQLite.

Schema (Phase 0):
  checkpoints(id, session_id, plan_step_id, state_json, created_at)

We use:
  - plan_step_id = turn label (e.g. "turn_3" or "step_s2_t1")
  - state_json   = JSON dump of {messages, metadata}
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from pure_agent.model import CanonicalMessage
from pure_agent.persistence.db import Database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "ckpt") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def messages_to_json(messages: list[CanonicalMessage]) -> str:
    """Serialize a list of CanonicalMessage to JSON."""
    out = []
    for m in messages:
        out.append(
            {
                "role": m.role.value,
                "content": [
                    {
                        "type": b.__class__.__name__,
                        **b.model_dump(),
                    }
                    for b in m.content
                ],
                "tool_call_id": m.tool_call_id,
                "synthetic": getattr(m, "synthetic", False),
            }
        )
    return json.dumps(out, ensure_ascii=False)


def messages_from_json(text: str) -> list[CanonicalMessage]:
    """Deserialize a list of CanonicalMessage from JSON."""
    from pure_agent.model import (
        Role,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    data = json.loads(text)
    out: list[CanonicalMessage] = []
    for raw in data:
        role = Role(raw["role"])
        content = []
        for b in raw.get("content", []):
            t = b.get("type")
            # TextBlock serializes with type='text' (the pydantic Literal)
            # but the JSON envelope uses the class name; we accept both.
            if t in ("TextBlock", "text"):
                content.append(TextBlock(text=b.get("text", "")))
            elif t in ("ToolUseBlock", "tool_use"):
                content.append(
                    ToolUseBlock(
                        tool_call_id=b.get("tool_call_id", ""),
                        name=b.get("name", ""),
                        arguments=b.get("arguments", {}),
                    )
                )
            elif t in ("ToolResultBlock", "tool_result"):
                content.append(
                    ToolResultBlock(
                        tool_call_id=b.get("tool_call_id", ""),
                        content=b.get("content", ""),
                        is_error=b.get("is_error", False),
                    )
                )
        out.append(
            CanonicalMessage(
                role=role,
                content=content,
                tool_call_id=raw.get("tool_call_id"),
                synthetic=raw.get("synthetic", False),
            )
        )
    return out


class Checkpointer:
    """Save / load conversation checkpoints to SQLite."""

    def __init__(self, db: Database, session_id: str) -> None:
        self.db = db
        # ensure session exists
        now = _now_iso()
        db.conn.execute(
            "INSERT OR IGNORE INTO sessions (id, project_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, "default", session_id, now, now),
        )
        self.session_id = session_id

    def save(
        self,
        messages: list[CanonicalMessage],
        *,
        turn_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Save a checkpoint. Returns the checkpoint id."""
        cid = _new_id()
        state = {
            "messages": json.loads(messages_to_json(messages)),
            "metadata": metadata or {},
        }
        state_json = json.dumps(state, ensure_ascii=False)
        self.db.conn.execute(
            "INSERT INTO checkpoints (id, session_id, plan_step_id, state_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, self.session_id, turn_id or "turn", state_json, _now_iso()),
        )
        return cid

    def load_latest(self) -> tuple[list[CanonicalMessage], dict] | None:
        """Load the most recent checkpoint for this session."""
        row = self.db.conn.execute(
            "SELECT * FROM checkpoints WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (self.session_id,),
        ).fetchone()
        if row is None:
            return None
        state = json.loads(row["state_json"])
        msgs = messages_from_json(json.dumps(state.get("messages", [])))
        meta = state.get("metadata", {})
        return msgs, meta

    def list_checkpoints(self) -> list[dict]:
        rows = self.db.conn.execute(
            "SELECT * FROM checkpoints WHERE session_id = ? "
            "ORDER BY created_at DESC",
            (self.session_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "turn_id": r["plan_step_id"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def clean(self) -> int:
        """Delete all checkpoints for this session. Returns rows deleted."""
        cur = self.db.conn.execute(
            "DELETE FROM checkpoints WHERE session_id = ?", (self.session_id,)
        )
        return cur.rowcount


__all__ = ["Checkpointer", "messages_from_json", "messages_to_json"]
