"""Tools layer: base, filesystem, search, web search, default registry."""

from pure_agent.tools.apply_patch import ApplyPatchTool
from pure_agent.tools.base import Tool, ToolRegistry, ToolResult
from pure_agent.tools.filesystem import (
    EditFileTool,
    ReadFileTool,
    Sandbox,
    WriteFileTool,
)
from pure_agent.tools.registry import build_default_registry
from pure_agent.tools.search import GlobTool, GrepTool
from pure_agent.tools.web_fetch import WebFetchParams, WebFetchTool
from pure_agent.tools.web_search import (
    DuckDuckGoProvider,
    SearchCache,
    TavilyProvider,
    WebSearchTool,
)

__all__ = [
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "ApplyPatchTool",
    "Sandbox",
    "GlobTool",
    "GrepTool",
    "WebSearchTool",
    "WebFetchTool",
    "WebFetchParams",
    "DuckDuckGoProvider",
    "TavilyProvider",
    "SearchCache",
    "build_default_registry",
]
