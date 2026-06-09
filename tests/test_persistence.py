"""Tests for persistence layer (schema, db)."""

from __future__ import annotations

import sqlite3

import pytest

from pure_agent.persistence import Database, apply_schema, connect, get_default_db_path


@pytest.mark.smoke
def test_default_db_path_under_home(tmp_home) -> None:
    from pathlib import Path
    p = get_default_db_path()
    # Resolve both sides (macOS symlinks /var -> /private/var)
    assert p == Path(tmp_home).resolve() / "memory.db"


@pytest.mark.smoke
def test_connect_creates_db_file(tmp_home) -> None:
    p = tmp_home / "test.db"
    conn = connect(p)
    try:
        assert p.exists()
        # PRAGMA applied
        cur = conn.execute("PRAGMA foreign_keys")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.smoke
def test_apply_schema_creates_all_tables(tmp_home) -> None:
    p = tmp_home / "test.db"
    db = Database(path=p)
    try:
        tables = db.table_names()
        # spot-check key tables
        assert "projects" in tables
        assert "sessions" in tables
        assert "messages" in tables
        assert "tool_calls" in tables
        assert "checkpoints" in tables
        assert "goals" in tables
        assert "plans" in tables
        assert "plan_steps" in tables
        assert "memory_short" in tables
        assert "memory_episodic" in tables
        assert "memory_semantic" in tables
        assert "memory_procedural" in tables
        assert "traces" in tables
        assert "retries" in tables
        # FTS5
        assert "messages_fts" in tables
        assert "memory_semantic_fts" in tables
        assert "plan_steps_fts" in tables
    finally:
        db.close()


@pytest.mark.smoke
def test_schema_version_tracked(tmp_home) -> None:
    p = tmp_home / "test.db"
    db = Database(path=p)
    try:
        v = db.schema_version()
        assert v == 1
    finally:
        db.close()


@pytest.mark.smoke
def test_schema_idempotent(tmp_home) -> None:
    """Applying schema twice doesn't error."""
    p = tmp_home / "test.db"
    conn = connect(p)
    try:
        apply_schema(conn)
        apply_schema(conn)  # second time
        # Still version 1
        cur = conn.execute("SELECT MAX(version) FROM schema_version")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.smoke
def test_transaction_rollback(tmp_home) -> None:
    """A failed transaction rolls back cleanly."""
    p = tmp_home / "test.db"
    db = Database(path=p)
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("p1", "test", "h1", "2026-01-01", "2026-01-01"),
            )
        # now intentionally fail
        try:
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO projects (id, name, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    ("p2", "test2", "h1", "2026-01-01", "2026-01-01"),  # duplicate hash
                )
        except sqlite3.IntegrityError:
            pass

        # p1 should still exist; p2 should not
        rows = db.conn.execute("SELECT id FROM projects").fetchall()
        ids = [r["id"] for r in rows]
        assert "p1" in ids
        assert "p2" not in ids
    finally:
        db.close()
