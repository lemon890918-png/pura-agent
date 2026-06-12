"""Aider-style repo_map tool.

Generates a compact index of the project's structure — files and their
top-level symbols (functions, classes, constants). The agent uses this
to understand the codebase layout before making multi-file edits.

Unlike Aider's full graph-ranking version, this is a simple linear
index with optional filter (path pattern). Token cost is bounded by
the per-file cap.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from pure_agent.tools.base import Tool, ToolResult
from pure_agent.tools.filesystem import Sandbox


class RepoMapParams(BaseModel):
    path_filter: str | None = Field(
        None,
        description=(
            "Glob pattern to filter files (e.g. 'src/**/*.py'). "
            "Defaults to all .py files under project root."
        ),
    )
    max_files: int = Field(
        50,
        ge=1,
        le=500,
        description="Maximum number of files to index (default 50)",
    )
    symbols_per_file: int = Field(
        15,
        ge=1,
        le=100,
        description="Max symbols per file (default 15)",
    )


# Skip common noise directories
_SKIP_DIRS = {
    "__pycache__", ".git", ".hg", ".svn", "node_modules", "venv", ".venv",
    "env", ".env", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "target", "vendor", ".tox", "site-packages",
    ".eggs", "*.egg-info",
}


def _iter_python_files(root: Path, pattern: str | None) -> list[Path]:
    if pattern:
        candidates = sorted(root.glob(pattern))
    else:
        candidates = []
        for p in root.rglob("*.py"):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            candidates.append(p)
    return [p for p in candidates if p.is_file()]


def _extract_symbols(path: Path) -> list[tuple[str, str]]:
    """Return [(name, kind), ...] for top-level defs in `path`."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    out: list[tuple[str, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, "def"))
        elif isinstance(node, ast.ClassDef):
            bases = ""
            if node.bases:
                base_strs = [ast.unparse(b) for b in node.bases]
                bases = "(" + ", ".join(base_strs) + ")"
            out.append((f"class {node.name}{bases}", "class"))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.append((t.id, "const"))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append((node.target.id, "const"))
    return out


class RepoMapTool(Tool):
    name: ClassVar[str] = "repo_map"
    description: ClassVar[str] = (
        "Build a compact index of the project's Python files and their "
        "top-level symbols (functions, classes, constants). Use this "
        "BEFORE making multi-file edits to understand the codebase layout. "
        "Output is a markdown-style list grouped by file. Pass path_filter "
        "to focus on a subdirectory (e.g. 'src/**/*.py'). Token-bounded."
    )
    parameters: ClassVar[dict] = RepoMapParams.model_json_schema()
    parameters_model: ClassVar[type[BaseModel]] = RepoMapParams

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()

    async def execute(
        self,
        path_filter: str | None = None,
        max_files: int = 50,
        symbols_per_file: int = 15,
    ) -> ToolResult:
        root = self.sandbox.root
        if path_filter:
            try:
                files = _iter_python_files(root, path_filter)
            except (OSError, ValueError) as e:
                return ToolResult.fail(f"glob error: {e}", code="glob_error")
        else:
            files = _iter_python_files(root, None)

        if not files:
            return ToolResult.ok_data(
                {"text": "(no Python files found)", "files_indexed": 0}
            )

        files = files[:max_files]
        lines: list[str] = [f"# Repository map: {root}", f"# {len(files)} files"]
        total_symbols = 0
        for f in files:
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            syms = _extract_symbols(f)[:symbols_per_file]
            if not syms:
                continue
            total_symbols += len(syms)
            lines.append(f"\n## {rel}")
            for name, kind in syms:
                lines.append(f"  - [{kind}] {name}")
        text = "\n".join(lines)
        return ToolResult.ok_data({
            "text": text,
            "files_indexed": len(files),
            "symbols_extracted": total_symbols,
            "truncated": len(files) == max_files,
        })


__all__ = ["RepoMapTool", "RepoMapParams"]
