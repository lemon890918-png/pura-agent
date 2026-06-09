"""Filesystem tools: read_file, write_file, edit_file.

These three are the "must do well" trio per the master plan. Heavy focus on
robustness: atomic writes, binary detection, sandbox enforcement, content hashing.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from pure_agent.tools.base import Tool, ToolResult


# ─── sandbox ──────────────────────────────────────────────────────────────────


class Sandbox:
    """Enforce that all file operations stay under a project root.

    Phase 1: project root = current working directory (or env override).
    Phase 5+: configurable per project, with explicit bypass.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        if root is None:
            root = os.environ.get("PURE_AGENT_PROJECT_ROOT") or os.getcwd()
        self.root = Path(root).expanduser().resolve()

    def resolve(self, path: str) -> Path:
        """Resolve user-supplied path against project root.

        Absolute paths must lie under root. Relative paths are joined to root.
        """
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise PermissionError(
                f"path {p} is outside project root {self.root}"
            )
        return p


# ─── read_file ────────────────────────────────────────────────────────────────


class ReadFileParams(BaseModel):
    path: str = Field(..., description="File path (relative to project root or absolute under it)")
    offset: int = Field(0, ge=0, description="Start line (0-indexed)")
    limit: int | None = Field(
        None, ge=1, le=10_000, description="How many lines to read (None = all remaining)"
    )


class ReadFileTool(Tool):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read a file's content. Returns text with line numbers plus metadata. "
        "Use offset/limit for large files. Binary files are detected and rejected."
    )
    parameters: ClassVar[dict] = ReadFileParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = ReadFileParams
    default_limit: ClassVar[int] = 2000

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()

    async def execute(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> ToolResult:
        try:
            p = self.sandbox.resolve(path)
        except PermissionError as e:
            return ToolResult.fail(str(e), code="out_of_project")

        if not p.exists():
            return ToolResult.fail(f"file not found: {p}", code="file_not_found")
        if not p.is_file():
            return ToolResult.fail(f"not a file: {p}", code="not_a_file")

        # binary detection
        try:
            with p.open("rb") as f:
                chunk = f.read(8192)
        except OSError as e:
            return ToolResult.fail(f"read error: {e}", code="io_error")
        if b"\x00" in chunk:
            return ToolResult.fail(
                f"binary file detected ({p.suffix}); use a specialized tool",
                code="is_binary",
            )
        # encoding
        try:
            text = chunk.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return ToolResult.fail(
                f"not valid utf-8: {p}", code="encoding_error"
            )

        try:
            full_text = p.read_text(encoding="utf-8")
        except OSError as e:
            return ToolResult.fail(f"read error: {e}", code="io_error")

        lines = full_text.splitlines(keepends=True)
        total = len(lines)
        lim = limit if limit is not None else self.default_limit
        end = min(offset + lim, total)
        slice_ = lines[offset:end]

        # Re-encode chunk to detect binary on the slice too
        numbered = "".join(f"{i+1+offset:6d}\t{line}" for i, line in enumerate(slice_))
        sha = hashlib.sha256(full_text.encode("utf-8")).hexdigest()

        return ToolResult.ok_data(
            {
                "content": numbered,
                "total_lines": total,
                "returned_lines": len(slice_),
                "is_binary": False,
                "truncated": end < total,
                "sha256": sha,
                "path": str(p),
            }
        )


# ─── write_file ───────────────────────────────────────────────────────────────


class WriteFileParams(BaseModel):
    path: str = Field(..., description="File path (relative to project root or absolute under it)")
    content: str = Field(..., description="Full file content to write")


class WriteFileTool(Tool):
    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = (
        "Write content to a file atomically (tmp + rename). Overwrites existing files. "
        "Creates parent directories if missing. Bounded by sandbox to project root."
    )
    parameters: ClassVar[dict] = WriteFileParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = WriteFileParams

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()

    async def execute(self, path: str, content: str) -> ToolResult:
        try:
            p = self.sandbox.resolve(path)
        except PermissionError as e:
            return ToolResult.fail(str(e), code="out_of_project")

        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()

        # atomic write: write to .tmp.<rand> in same dir, then rename
        try:
            tmp_dir = p.parent
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=tmp_dir,
                prefix=f".{p.name}.",
                suffix=".tmp",
                delete=False,
            ) as tf:
                tf.write(data)
                tmp_name = tf.name
            os.replace(tmp_name, p)
        except OSError as e:
            # cleanup tmp on failure
            try:
                if "tmp_name" in locals() and os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass
            return ToolResult.fail(f"write error: {e}", code="io_error")

        return ToolResult.ok_data(
            {
                "path": str(p),
                "bytes_written": len(data),
                "sha256": sha,
            }
        )


# ─── edit_file ────────────────────────────────────────────────────────────────


class EditFileParams(BaseModel):
    path: str = Field(..., description="File path")
    old_string: str = Field(..., min_length=1, description="Exact text to find")
    new_string: str = Field(..., description="Replacement text")
    replace_all: bool = Field(False, description="If True, replace all occurrences")


class EditFileTool(Tool):
    name: ClassVar[str] = "edit_file"
    description: ClassVar[str] = (
        "Edit a file by replacing old_string with new_string. By default replaces "
        "exactly one occurrence; pass replace_all=true for global replace. Atomic write."
    )
    parameters: ClassVar[dict] = EditFileParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = EditFileParams

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()
        self._write_tool = WriteFileTool(sandbox)

    async def execute(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        try:
            p = self.sandbox.resolve(path)
        except PermissionError as e:
            return ToolResult.fail(str(e), code="out_of_project")

        if not p.exists():
            return ToolResult.fail(f"file not found: {p}", code="file_not_found")

        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return ToolResult.fail(f"read error: {e}", code="io_error")

        count = text.count(old_string)
        if count == 0:
            return ToolResult.fail(
                f"old_string not found in {p}", code="old_string_not_found"
            )
        if count > 1 and not replace_all:
            return ToolResult.fail(
                f"old_string matches {count} times; pass replace_all=true or be more specific",
                code="ambiguous_match",
            )

        if replace_all:
            new_text = text.replace(old_string, new_string)
            replacements = count
        else:
            new_text = text.replace(old_string, new_string, 1)
            replacements = 1

        result = await self._write_tool.execute(path=p, content=new_text)
        if not result.ok:
            return result
        return ToolResult.ok_data(
            {
                "path": str(p),
                "replacements": replacements,
                "old_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "new_sha256": hashlib.sha256(new_text.encode("utf-8")).hexdigest(),
            }
        )


__all__ = [
    "Sandbox",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
]
