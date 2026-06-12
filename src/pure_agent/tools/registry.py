"""Default tool registry builder.

Returns a fresh ToolRegistry populated with the 5 baseline tools.
"""

from __future__ import annotations

from pure_agent.tools.apply_patch import ApplyPatchTool
from pure_agent.tools.base import ToolRegistry
from pure_agent.tools.filesystem import EditFileTool, ReadFileTool, Sandbox, WriteFileTool
from pure_agent.tools.repo_map import RepoMapTool
from pure_agent.tools.search import GlobTool, GrepTool
from pure_agent.tools.web_fetch import WebFetchTool
from pure_agent.tools.web_search import WebSearchTool


def build_default_registry(
    sandbox: Sandbox | None = None,
    brave_api_key: str | None = None,
    tavily_api_key: str | None = None,
) -> ToolRegistry:
    """Construct the default tool set.

    Includes:
      - read_file, write_file, edit_file, apply_patch (Codex-style)
      - glob, grep
      - repo_map (Aider-style AST index)
      - web_search  (Tavily > Brave > DDG by priority)
      - web_fetch   (Phase 9+: download URL → text)
    """
    sbox = sandbox or Sandbox()
    reg = ToolRegistry()
    reg.register(ReadFileTool(sbox))
    reg.register(WriteFileTool(sbox))
    reg.register(EditFileTool(sbox))
    reg.register(ApplyPatchTool(sbox))
    reg.register(GlobTool(sbox))
    reg.register(GrepTool(sbox))
    reg.register(RepoMapTool(sbox))
    reg.register(WebSearchTool(brave_api_key=brave_api_key, tavily_api_key=tavily_api_key))
    reg.register(WebFetchTool())
    return reg


__all__ = ["build_default_registry"]
