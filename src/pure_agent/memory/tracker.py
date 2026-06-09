"""File tracker for diff-only re-read optimization.

When the agent reads a file, we record the path + mtime + size + content hash.
If the same file is read again in the same session, and mtime + size + hash
are unchanged, we can:
  - mark the read as cached
  - optionally return just a diff (Phase 3 stub: log only)

When the file changes (mtime / size / hash differ), we treat it as a fresh
read and update the tracker.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pure_agent.persistence.db import Database


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "ft") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def compute_content_hash(path: str) -> str:
    """SHA-256 hex of file contents."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


@dataclass
class FileState:
    path: str
    mtime: float
    size: int
    content_hash: str
    cached: bool = False

    def is_unchanged(self, other: "FileState") -> bool:
        return (
            self.mtime == other.mtime
            and self.size == other.size
            and self.content_hash == other.content_hash
        )


class FileTracker:
    """Tracks file read state per session for caching/diff optimization."""

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

    def _stat_file(self, path: str) -> FileState | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return FileState(
            path=path,
            mtime=st.st_mtime,
            size=st.st_size,
            content_hash=compute_content_hash(path),
        )

    def lookup(self, path: str) -> FileState | None:
        """Read the current state of a file and compare to stored.

        Returns:
          - FileState(cached=True) if file is unchanged from last read
          - FileState(cached=False) if file is new or has changed
          - None if file doesn't exist
        """
        cur = self._stat_file(path)
        if cur is None:
            return None
        row = self.db.conn.execute(
            "SELECT * FROM file_tracker WHERE session_id = ? AND path = ?",
            (self.session_id, path),
        ).fetchone()
        if row is None:
            # never seen this file
            cur.cached = False
            self._upsert(cur)
            return cur
        prev = FileState(
            path=row["path"],
            mtime=row["mtime"],
            size=row["size"],
            content_hash=row["content_hash"],
        )
        cur.cached = prev.is_unchanged(cur)
        self._upsert(cur)
        return cur

    def _upsert(self, state: FileState) -> None:
        now = _now_iso()
        existing = self.db.conn.execute(
            "SELECT id FROM file_tracker WHERE session_id = ? AND path = ?",
            (self.session_id, state.path),
        ).fetchone()
        if existing:
            self.db.conn.execute(
                "UPDATE file_tracker SET mtime=?, size=?, content_hash=?, last_read_at=? "
                "WHERE session_id = ? AND path = ?",
                (state.mtime, state.size, state.content_hash, now, self.session_id, state.path),
            )
        else:
            self.db.conn.execute(
                "INSERT INTO file_tracker (id, session_id, path, mtime, size, content_hash, last_read_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _new_id(),
                    self.session_id,
                    state.path,
                    state.mtime,
                    state.size,
                    state.content_hash,
                    now,
                ),
            )

    def invalidate(self, path: str) -> None:
        """Forget a file (e.g., on external edit)."""
        self.db.conn.execute(
            "DELETE FROM file_tracker WHERE session_id = ? AND path = ?",
            (self.session_id, path),
        )

    def all_tracked(self) -> list[dict]:
        rows = self.db.conn.execute(
            "SELECT * FROM file_tracker WHERE session_id = ? ORDER BY last_read_at DESC",
            (self.session_id,),
        ).fetchall()
        return [
            {
                "path": r["path"],
                "mtime": r["mtime"],
                "size": r["size"],
                "content_hash": r["content_hash"],
                "last_read_at": r["last_read_at"],
            }
            for r in rows
        ]


__all__ = ["FileTracker", "FileState", "compute_content_hash"]
