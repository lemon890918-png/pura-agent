"""SQLite database wrapper (sync, Phase 0)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_default_db_path() -> Path:
    """Default memory db path: ~/.pure-agent/memory.db."""
    from pure_agent.config import get_home

    return get_home() / "memory.db"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults."""
    if db_path is None:
        db_path = get_default_db_path()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,  # autocommit, we manage transactions explicitly
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Apply the bundled schema.sql to a connection. Idempotent."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


@dataclass
class Database:
    """Lightweight wrapper around a single sqlite3 connection (Phase 0: sync)."""

    path: Path
    _conn: sqlite3.Connection | None = None

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("Database.path is required")

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = connect(self.path)
            apply_schema(self._conn)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Wrap a series of statements in a transaction."""
        c = self.conn
        c.execute("BEGIN")
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    def table_names(self) -> list[str]:
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r["name"] for r in cur.fetchall()]

    def schema_version(self) -> int | None:
        cur = self.conn.execute("SELECT MAX(version) AS v FROM schema_version")
        row = cur.fetchone()
        if row is None:
            return None
        v = row["v"]
        return int(v) if v is not None else None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
