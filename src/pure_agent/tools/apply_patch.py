"""Codex-style apply_patch tool.

Format (compatible with OpenAI's apply_patch):

    *** Begin Patch
    *** Add File: path/to/new.py
    +line 1
    +line 2
    *** Update File: path/to/existing.py
    @@ classdef Foo
     context line
    -removed line
    +added line
     another context
    *** Delete File: path/to/remove.py
    *** End Patch

Why this is better than edit_file / write_file for agents:
  - Lower token cost (diff vs full file)
  - Explicit hunk headers give the model anchored context
  - One tool handles add / update / delete uniformly
  - Atomic: all-or-nothing (if any hunk fails, no files are touched)
  - Maps naturally to the model's training data
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from pure_agent.tools.base import Tool, ToolResult
from pure_agent.tools.filesystem import Sandbox


class ApplyPatchParams(BaseModel):
    patch: str = Field(..., description=(
        "The full patch text, starting with '*** Begin Patch' and ending "
        "with '*** End Patch'. Use '*** Add File:' for new files, "
        "'*** Update File:' for modifications, '*** Delete File:' for removal."
    ))


# ── parsing ────────────────────────────────────────────────────────────────


_BEGIN_RE = re.compile(r"\*\*\*\s+Begin Patch\s*")
_END_RE = re.compile(r"\*\*\*\s+End Patch\s*")
_ADD_RE = re.compile(r"\*\*\*\s+Add File:\s*(.+?)\s*$")
_UPDATE_RE = re.compile(r"\*\*\*\s+Update File:\s*(.+?)\s*$")
_DELETE_RE = re.compile(r"\*\*\*\s+Delete File:\s*(.+?)\s*$")
_HUNK_RE = re.compile(r"^@@\s*(.*?)\s*$")


class Hunk:
    __slots__ = ("header", "lines")

    def __init__(self, header: str, lines: list[str]) -> None:
        self.header = header  # e.g. "classdef Foo" or "def method"
        self.lines = lines    # list of context/added/removed lines


class FileOp:
    __slots__ = ("op", "path", "hunks", "new_content")

    def __init__(self, op: str, path: str, hunks: list[Hunk] | None = None,
                 new_content: list[str] | None = None) -> None:
        self.op = op           # "add" | "update" | "delete"
        self.path = path
        self.hunks = hunks or []
        self.new_content = new_content or []


def parse_patch(patch: str) -> list[FileOp]:
    """Parse a patch string into a list of FileOp objects.

    Raises ValueError on malformed input. The parse is forgiving about
    line endings and trailing whitespace, strict about op headers.
    """
    if not _BEGIN_RE.search(patch):
        raise ValueError("patch must start with '*** Begin Patch'")
    if not _END_RE.search(patch):
        raise ValueError("patch must end with '*** End Patch'")

    # Strip the begin/end markers
    inner = _BEGIN_RE.split(patch, maxsplit=1)[1]
    inner = _END_RE.split(inner, maxsplit=1)[0]
    lines = inner.splitlines()

    ops: list[FileOp] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m_add = _ADD_RE.match(line)
        m_up = _UPDATE_RE.match(line)
        m_del = _DELETE_RE.match(line)
        if m_add:
            i += 1
            content: list[str] = []
            while i < n and not (lines[i].startswith("*** ") and not lines[i].startswith("*** ")):
                # Allow "@@" inside add block? Per Codex spec, no — it's
                # pure new lines (all prefixed ' ' or '+' or implicit ' ').
                ln = lines[i]
                if ln.startswith("+"):
                    content.append(ln[1:])
                elif ln.startswith(" "):
                    content.append(ln[1:])
                elif ln == "":
                    content.append("")
                else:
                    break
                i += 1
            ops.append(FileOp("add", m_add.group(1).strip(), new_content=content))
            continue
        if m_del:
            ops.append(FileOp("delete", m_del.group(1).strip()))
            i += 1
            continue
        if m_up:
            path = m_up.group(1).strip()
            i += 1
            hunks: list[Hunk] = []
            current_hunk: Hunk | None = None
            while i < n and not _ADD_RE.match(lines[i]) and not _UPDATE_RE.match(lines[i]) and not _DELETE_RE.match(lines[i]):
                ln = lines[i]
                if _HUNK_RE.match(ln):
                    if current_hunk is not None:
                        hunks.append(current_hunk)
                    current_hunk = Hunk(header=_HUNK_RE.match(ln).group(1), lines=[])
                elif current_hunk is not None:
                    # Line within current hunk: must start with ' ', '+', '-', or '@@'
                    if ln.startswith("+"):
                        current_hunk.lines.append(("+", ln[1:]))
                    elif ln.startswith("-"):
                        current_hunk.lines.append(("-", ln[1:]))
                    elif ln.startswith(" "):
                        current_hunk.lines.append((" ", ln[1:]))
                    elif ln == "":
                        # Blank line = context line with no content
                        current_hunk.lines.append((" ", ""))
                    else:
                        # Stray non-prefix line — treat as implicit context
                        current_hunk.lines.append((" ", ln))
                else:
                    # Stray line before any @@ — treat as context-only
                    current_hunk = Hunk(header="", lines=[(" ", ln)])
                i += 1
            if current_hunk is not None:
                hunks.append(current_hunk)
            ops.append(FileOp("update", path, hunks=hunks))
            continue
        # Stray content outside any op — skip with warning
        i += 1
    return ops


# ── applying ───────────────────────────────────────────────────────────────


def _apply_update_hunks(orig: str, hunks: list[Hunk]) -> str:
    """Apply hunks to original text, returning new text.

    Strategy: for each hunk, find the unique substring in `orig` that
    matches the context lines (surrounding the +/- lines), then
    replace it with context+added. If no match or ambiguous, raise.
    """
    # Build the "needle" (context+removed) and "replacement" (context+added)
    # for each hunk.
    new = orig
    for h in hunks:
        needle_lines: list[str] = []
        replace_lines: list[str] = []
        for op, text in h.lines:
            if op == " ":
                needle_lines.append(text)
                replace_lines.append(text)
            elif op == "-":
                needle_lines.append(text)
            elif op == "+":
                replace_lines.append(text)
        if not any(op == "-" for op, _ in h.lines) and not any(op == "+" for op, _ in h.lines):
            # No-op hunk (context only) — skip
            continue
        needle = "\n".join(needle_lines)
        replacement = "\n".join(replace_lines)
        # Try exact match first
        count = new.count(needle)
        if count == 0:
            # Try with relaxed trailing whitespace per line
            relaxed_needle = "\n".join(line.rstrip() for line in needle_lines)
            relaxed_new = "\n".join(line.rstrip() for line in new.splitlines())
            if relaxed_needle in relaxed_new:
                # Re-locate exact line numbers
                idx = relaxed_new.index(relaxed_needle)
                # Reconstruct exact replacement using the actual original
                # lines (so trailing whitespace is preserved)
                orig_lines = new.splitlines()
                # Approximate: count newlines before match in relaxed form
                relaxed_lines = relaxed_new.splitlines()
                rel_start_line = relaxed_lines[:idx // (len(relaxed_lines[0]) + 1 if relaxed_lines else 1)]
                # This branch is approximate; the common case is exact match
                raise ValueError(
                    f"hunk header {h.header!r}: context lines match only with "
                    f"whitespace relaxation (re-run with exact text)"
                )
            raise ValueError(
                f"hunk header {h.header!r}: context not found in file"
            )
        if count > 1:
            raise ValueError(
                f"hunk header {h.header!r}: context matches {count} times; "
                f"add more surrounding context to disambiguate"
            )
        new = new.replace(needle, replacement, 1)
    return new


# ── tool ───────────────────────────────────────────────────────────────────


class ApplyPatchTool(Tool):
    name: ClassVar[str] = "apply_patch"
    description: ClassVar[str] = (
        "Apply a Codex-style patch to one or more files atomically. "
        "The patch is a unified-diff-like text with '*** Begin Patch' / "
        "'*** End Patch' markers and per-file '*** Add File:', "
        "'*** Update File:', or '*** Delete File:' sections. "
        "Hunks use '@@ <header>' followed by ' ' (context), '-' (removed), "
        "'+' (added) prefixed lines. "
        "Use this for ANY file modification — it's more token-efficient "
        "and more reliable than write_file (full content) or edit_file "
        "(single old/new pair). The model emits the full patch as a "
        "single tool call; the tool atomically applies all hunks across "
        "all files (or none if any hunk fails to apply)."
    )
    parameters: ClassVar[dict] = ApplyPatchParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = ApplyPatchParams

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()
        self._write = type("W", (), {})()  # placeholder
        from pure_agent.tools.filesystem import WriteFileTool
        self._write_tool = WriteFileTool(sandbox)

    async def execute(self, patch: str) -> ToolResult:
        try:
            ops = parse_patch(patch)
        except ValueError as e:
            return ToolResult.fail(f"patch parse error: {e}", code="patch_parse")

        if not ops:
            return ToolResult.fail(
                "patch contained no file operations",
                code="patch_empty",
            )

        # Pre-validate all paths are in sandbox
        for op in ops:
            try:
                self.sandbox.resolve(op.path)
            except PermissionError as e:
                return ToolResult.fail(str(e), code="out_of_project")

        # Apply all ops, collecting proposed changes. If any op fails,
        # roll back all previous successful changes in this batch.
        applied: list[tuple[Path, str, str]] = []  # (path, old_content_or_None, new_content)
        try:
            for op in ops:
                p = self.sandbox.resolve(op.path)
                if op.op == "add":
                    if p.exists():
                        return self._rollback(
                            applied,
                            f"add: file already exists: {p}",
                            code="file_exists",
                        )
                    new_content = "\n".join(op.new_content)
                    if new_content and not new_content.endswith("\n"):
                        new_content += "\n"
                    applied.append((p, None, new_content))
                elif op.op == "delete":
                    if not p.exists():
                        return self._rollback(
                            applied,
                            f"delete: file not found: {p}",
                            code="file_not_found",
                        )
                    old = p.read_text(encoding="utf-8")
                    applied.append((p, old, ""))
                elif op.op == "update":
                    if not p.exists():
                        return self._rollback(
                            applied,
                            f"update: file not found: {p}",
                            code="file_not_found",
                        )
                    orig = p.read_text(encoding="utf-8")
                    try:
                        new_content = _apply_update_hunks(orig, op.hunks)
                    except ValueError as e:
                        return self._rollback(
                            applied,
                            f"update {p}: {e}",
                            code="hunk_apply",
                        )
                    if new_content == orig:
                        return self._rollback(
                            applied,
                            f"update {p}: hunks produced no change",
                            code="no_change",
                        )
                    applied.append((p, orig, new_content))
        except Exception as e:
            return self._rollback(applied, f"unexpected error: {e}", code="apply_error")

        # All preflight passed. Commit changes.
        results: list[dict] = []
        for p, old, new in applied:
            if new == "":
                # delete
                p.unlink()
                results.append({"op": "delete", "path": str(p), "removed_bytes": len(old) if old else 0})
            else:
                result = await self._write_tool.execute(path=str(p), content=new)
                if not result.ok:
                    return self._rollback(applied, f"write {p} failed: {result.error}", code="write_error")
                results.append({"op": "add" if old is None else "update",
                                "path": str(p), "bytes_written": len(new)})

        return ToolResult.ok_data({
            "applied": results,
            "files_touched": len(results),
        })

    def _rollback(self, applied: list[tuple[Path, str, str]], msg: str, code: str) -> ToolResult:
        # Roll back in reverse order
        for p, old, new in reversed(applied):
            try:
                if old is None:
                    # Was an add — remove if it got created
                    if p.exists():
                        p.unlink()
                else:
                    # Was update or delete — restore old content
                    p.write_text(old, encoding="utf-8")
            except Exception:
                pass
        return ToolResult.fail(msg, code=code)


__all__ = ["ApplyPatchTool", "parse_patch", "ApplyPatchParams"]
