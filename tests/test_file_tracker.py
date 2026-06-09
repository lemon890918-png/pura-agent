"""Tests for FileTracker."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from pure_agent.memory import FileTracker, compute_content_hash
from pure_agent.persistence import Database


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(path=tmp_path / "test.db")
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    # pre-create project + session for tests
    d.conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("default", "Default", "default", now, now),
    )
    d.conn.execute(
        "INSERT OR IGNORE INTO sessions (id, project_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1", "default", "s1", now, now),
    )
    return d


@pytest.fixture
def tracker(db, tmp_path) -> tuple[FileTracker, Path]:
    return FileTracker(db, "s1"), tmp_path


def _write(p: Path, content: str) -> None:
    p.write_text(content)


@pytest.mark.smoke
def test_first_read_not_cached(tracker) -> None:
    ft, tmp = tracker
    f = tmp / "test.txt"
    _write(f, "hello")
    state = ft.lookup(str(f))
    assert state is not None
    assert state.cached is False


@pytest.mark.smoke
def test_second_read_unchanged_cached(tracker) -> None:
    ft, tmp = tracker
    f = tmp / "test.txt"
    _write(f, "hello")
    ft.lookup(str(f))  # first
    state2 = ft.lookup(str(f))  # second
    assert state2.cached is True


@pytest.mark.smoke
def test_modified_file_not_cached(tracker) -> None:
    ft, tmp = tracker
    f = tmp / "test.txt"
    _write(f, "v1")
    ft.lookup(str(f))
    # bump mtime
    time.sleep(0.05)
    _write(f, "v2")
    state = ft.lookup(str(f))
    assert state.cached is False


@pytest.mark.smoke
def test_missing_file_returns_none(tracker) -> None:
    ft, tmp = tracker
    state = ft.lookup(str(tmp / "nonexistent.txt"))
    assert state is None


@pytest.mark.smoke
def test_content_hash_stable() -> None:
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("hello")
        path = f.name
    try:
        h1 = compute_content_hash(path)
        h2 = compute_content_hash(path)
        assert h1 == h2
        assert len(h1) == 64
    finally:
        os.unlink(path)


@pytest.mark.smoke
def test_invalidate(tracker) -> None:
    ft, tmp = tracker
    f = tmp / "x.txt"
    _write(f, "v1")
    ft.lookup(str(f))
    ft.invalidate(str(f))
    state = ft.lookup(str(f))
    assert state.cached is False  # next read re-tracks


@pytest.mark.smoke
def test_all_tracked(tracker) -> None:
    ft, tmp = tracker
    (tmp / "a").write_text("a")
    (tmp / "b").write_text("b")
    ft.lookup(str(tmp / "a"))
    ft.lookup(str(tmp / "b"))
    tracked = ft.all_tracked()
    assert len(tracked) == 2
