"""Tests for filesystem tools (read_file, write_file, edit_file)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from pure_agent.tools import EditFileTool, ReadFileTool, Sandbox, WriteFileTool


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(root=tmp_path)


@pytest.fixture
def read(sandbox: Sandbox) -> ReadFileTool:
    return ReadFileTool(sandbox)


@pytest.fixture
def write(sandbox: Sandbox) -> WriteFileTool:
    return WriteFileTool(sandbox)


@pytest.fixture
def edit(sandbox: Sandbox) -> EditFileTool:
    return EditFileTool(sandbox)


@pytest.mark.smoke
def test_read_basic(read, tmp_path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hello\nworld\n")
    r = run(read.execute(path=str(p)))
    assert r.ok
    assert "hello" in r.data["content"]
    assert r.data["total_lines"] == 2


@pytest.mark.smoke
def test_read_offset_limit(read, tmp_path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("\n".join(str(i) for i in range(100)))
    r = run(read.execute(path=str(p), offset=10, limit=5))
    assert r.ok
    assert r.data["returned_lines"] == 5
    assert r.data["total_lines"] == 100
    # content should contain lines 11-15
    assert "11" in r.data["content"]
    assert "15" in r.data["content"]
    assert "16" not in r.data["content"]


@pytest.mark.smoke
def test_read_nonexistent(read) -> None:
    r = run(read.execute(path="missing.txt"))
    assert not r.ok
    assert r.error_code == "file_not_found"


@pytest.mark.smoke
def test_read_binary_detected(read, tmp_path) -> None:
    p = tmp_path / "blob.bin"
    p.write_bytes(b"\x00\x01\x02\x03\xff")
    r = run(read.execute(path=str(p)))
    assert not r.ok
    assert r.error_code == "is_binary"


@pytest.mark.smoke
def test_read_sandbox_rejects_escape(read, tmp_path) -> None:
    r = run(read.execute(path="/etc/passwd"))
    assert not r.ok
    assert r.error_code == "out_of_project"


@pytest.mark.smoke
def test_write_creates_file(write, tmp_path) -> None:
    p = tmp_path / "new.txt"
    r = run(write.execute(path=str(p), content="hello\n"))
    assert r.ok
    assert p.exists()
    assert p.read_text() == "hello\n"
    assert len(r.data["sha256"]) == 64


@pytest.mark.smoke
def test_write_overwrites(write, tmp_path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("old")
    r = run(write.execute(path=str(p), content="new"))
    assert r.ok
    assert p.read_text() == "new"


@pytest.mark.smoke
def test_write_atomic_no_partial(write, tmp_path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("orig")
    # Force a write that should be atomic
    r = run(write.execute(path=str(p), content="x" * 5000))
    assert r.ok
    # No leftover tmp files
    leftovers = [q for q in tmp_path.iterdir() if q.name.startswith(".") and q.suffix == ".tmp"]
    assert not leftovers


@pytest.mark.smoke
def test_write_sandbox_rejects_escape(write) -> None:
    r = run(write.execute(path="/tmp/escape.txt", content="x"))
    assert not r.ok
    assert r.error_code == "out_of_project"


@pytest.mark.smoke
def test_write_creates_parent_dirs(write, tmp_path) -> None:
    p = tmp_path / "a" / "b" / "c.txt"
    r = run(write.execute(path=str(p), content="hi"))
    assert r.ok
    assert p.exists()


@pytest.mark.smoke
def test_edit_single_replacement(edit, tmp_path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("hello world\n")
    r = run(edit.execute(path=str(p), old_string="hello", new_string="goodbye"))
    assert r.ok
    assert r.data["replacements"] == 1
    assert p.read_text() == "goodbye world\n"


@pytest.mark.smoke
def test_edit_not_found(edit, tmp_path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("hello")
    r = run(edit.execute(path=str(p), old_string="zzz", new_string="yyy"))
    assert not r.ok
    assert r.error_code == "old_string_not_found"


@pytest.mark.smoke
def test_edit_ambiguous_rejected(edit, tmp_path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("aaa\naaa\naaa\n")
    r = run(edit.execute(path=str(p), old_string="aaa", new_string="bbb"))
    assert not r.ok
    assert r.error_code == "ambiguous_match"


@pytest.mark.smoke
def test_edit_replace_all(edit, tmp_path) -> None:
    p = tmp_path / "x.txt"
    p.write_text("aaa\naaa\naaa\n")
    r = run(
        edit.execute(path=str(p), old_string="aaa", new_string="bbb", replace_all=True)
    )
    assert r.ok
    assert r.data["replacements"] == 3
    assert p.read_text() == "bbb\nbbb\nbbb\n"


@pytest.mark.smoke
def test_tool_schemas_have_validation(read, write, edit) -> None:
    """All three tools have a pydantic parameters_model and reject bad args."""
    for tool in [read, write, edit]:
        assert tool.parameters_model is not None
        bad, err = tool.validate_args({"bad": "input"})
        assert err is not None
        assert "Invalid" in err
