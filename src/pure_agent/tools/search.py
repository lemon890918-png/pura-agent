"""Search tools: glob, grep."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from pure_agent.tools.base import Tool, ToolResult
from pure_agent.tools.filesystem import Sandbox


# Directories to skip during search
_DEFAULT_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".eggs",
        ".tox",
    }
)


# ─── glob ─────────────────────────────────────────────────────────────────────


class GlobParams(BaseModel):
    pattern: str = Field(..., description="Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'")
    path: str = Field(".", description="Search root (relative to project root)")
    limit: int = Field(100, ge=1, le=1000, description="Max results")


class GlobTool(Tool):
    name: ClassVar[str] = "glob"
    description: ClassVar[str] = (
        "Find files matching a glob pattern. Skips common build/venv dirs. "
        "Returns absolute paths."
    )
    parameters: ClassVar[dict] = GlobParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = GlobParams

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()

    async def execute(self, pattern: str, path: str = ".", limit: int = 100) -> ToolResult:
        try:
            root = self.sandbox.resolve(path)
        except PermissionError as e:
            return ToolResult.fail(str(e), code="out_of_project")
        if not root.exists():
            return ToolResult.fail(f"path not found: {root}", code="path_not_found")

        try:
            all_matches = [p for p in root.glob(pattern) if p.is_file()]
        except (ValueError, OSError) as e:
            return ToolResult.fail(f"glob error: {e}", code="glob_error")

        # filter out skip dirs
        filtered: list[Path] = []
        for m in all_matches:
            if any(part in _DEFAULT_SKIP_DIRS for part in m.parts):
                continue
            filtered.append(m)

        total = len(filtered)
        truncated = total > limit
        sliced = filtered[:limit]
        return ToolResult.ok_data(
            {
                "matches": [str(p) for p in sliced],
                "truncated": truncated,
                "total": total,
            }
        )


# ─── grep ─────────────────────────────────────────────────────────────────────


class GrepParams(BaseModel):
    pattern: str = Field(..., description="Python regular expression")
    path: str = Field(".", description="Search root (relative to project root)")
    include_glob: str | None = Field(None, description="Only search files matching this glob")
    limit: int = Field(100, ge=1, le=1000, description="Max matches to return")


class GrepTool(Tool):
    name: ClassVar[str] = "grep"
    description: ClassVar[str] = (
        "Search files for a regex pattern. Returns file path, line number, and matching text. "
        "Skips common build/venv dirs. Use include_glob to scope to specific file types."
    )
    parameters: ClassVar[dict] = GrepParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = GrepParams

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        include_glob: str | None = None,
        limit: int = 100,
    ) -> ToolResult:
        try:
            root = self.sandbox.resolve(path)
        except PermissionError as e:
            return ToolResult.fail(str(e), code="out_of_project")
        if not root.exists():
            return ToolResult.fail(f"path not found: {root}", code="path_not_found")

        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult.fail(f"invalid regex: {e}", code="regex_error")

        # collect files to scan
        if root.is_file():
            files: list[Path] = [root]
        else:
            files = [
                p
                for p in root.rglob("*")
                if p.is_file()
                and not any(part in _DEFAULT_SKIP_DIRS for part in p.parts)
            ]
            if include_glob:
                files = [f for f in files if f.match(include_glob)]

        matches: list[dict[str, Any]] = []
        truncated = False
        for f in files:
            try:
                # skip binary
                with f.open("rb") as fh:
                    head = fh.read(8192)
                if b"\x00" in head:
                    continue
                text = head.decode("utf-8", errors="ignore")
            except OSError:
                continue
            try:
                full = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(full.splitlines(), start=1):
                if rx.search(line):
                    matches.append(
                        {
                            "path": str(f),
                            "line": lineno,
                            "text": line[:500],
                        }
                    )
                    if len(matches) >= limit:
                        truncated = True
                        break
            if truncated:
                break

        return ToolResult.ok_data(
            {
                "matches": matches,
                "truncated": truncated,
                "total": len(matches),
            }
        )


__all__ = ["GlobTool", "GrepTool"]
