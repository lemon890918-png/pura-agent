"""Tests for glob/grep tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pure_agent.tools import GlobTool, GrepTool, Sandbox


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def sandbox_with_files(tmp_path: Path) -> Sandbox:
    # create some structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("print('a')\n")
    (tmp_path / "src" / "b.py").write_text("def b():\n    pass\n")
    (tmp_path / "src" / "c.txt").write_text("hello\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("ignored")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignored")
    return Sandbox(root=tmp_path)


@pytest.mark.smoke
def test_glob_py_files(sandbox_with_files) -> None:
    t = GlobTool(sandbox_with_files)
    r = run(t.execute(pattern="**/*.py"))
    assert r.ok
    matches = r.data["matches"]
    assert any(m.endswith("a.py") for m in matches)
    assert any(m.endswith("b.py") for m in matches)
    # node_modules/.git should be skipped
    assert not any("node_modules" in m for m in matches)
    assert not any(".git" in m for m in matches)


@pytest.mark.smoke
def test_glob_limit(sandbox_with_files) -> None:
    t = GlobTool(sandbox_with_files)
    r = run(t.execute(pattern="**/*", limit=1))
    assert r.ok
    assert r.data["truncated"] is True
    assert len(r.data["matches"]) == 1


@pytest.mark.smoke
def test_glob_bad_pattern(sandbox_with_files) -> None:
    """Glob accepts most patterns silently; we don't strictly reject, but invalid
    relative paths (e.g. through escape) should be caught by sandbox."""
    t = GlobTool(sandbox_with_files)
    # An invalid pattern in Python's glob doesn't raise; it just yields nothing.
    r = run(t.execute(pattern="[invalid"))
    # Behavior: no match, ok=True, empty list.
    # What we DO want to catch is escape via path:
    r2 = run(t.execute(pattern="*", path="/etc"))
    assert not r2.ok
    assert r2.error_code == "out_of_project"


@pytest.mark.smoke
def test_grep_basic(sandbox_with_files) -> None:
    t = GrepTool(sandbox_with_files)
    r = run(t.execute(pattern="def b"))
    assert r.ok
    assert any("b.py" in m["path"] for m in r.data["matches"])


@pytest.mark.smoke
def test_grep_include_glob(sandbox_with_files) -> None:
    t = GrepTool(sandbox_with_files)
    # 'hello' is in c.txt only
    r = run(t.execute(pattern="hello", include_glob="*.py"))
    assert r.ok
    assert r.data["matches"] == []


@pytest.mark.smoke
def test_grep_invalid_regex(sandbox_with_files) -> None:
    t = GrepTool(sandbox_with_files)
    r = run(t.execute(pattern="[bad"))
    assert not r.ok
    assert r.error_code == "regex_error"


@pytest.mark.smoke
def test_grep_skips_binary(sandbox_with_files, tmp_path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02hello")
    t = GrepTool(sandbox_with_files)
    r = run(t.execute(pattern="hello"))
    assert r.ok
    # should find hello in c.txt only
    assert all(".bin" not in m["path"] for m in r.data["matches"])


@pytest.mark.smoke
def test_sandbox_blocks_escape(sandbox_with_files) -> None:
    t = GlobTool(sandbox_with_files)
    r = run(t.execute(pattern="*", path="/etc"))
    assert not r.ok
    assert r.error_code == "out_of_project"
