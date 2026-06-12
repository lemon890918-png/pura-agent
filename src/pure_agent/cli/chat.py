"""`pure-agent chat` — interactive / single-prompt chat with the LLM."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live

from pure_agent.agent import AIAgentLoop
from pure_agent.config import get_home, load_config
from pure_agent.logging import get_logger, setup_logging
from pure_agent.mcp import MCPToolAdapter
from pure_agent.model import MinimaxAdapter
from pure_agent.model.token_counter import estimate_request_tokens
from pure_agent.tools import build_default_registry
from pure_agent.tools.base import Tool, ToolResult


class _LazyMCPProxy(Tool):
    """A placeholder Tool that lists MCP tools without starting the server.

    On first execute(), starts the server in the calling event loop and
    delegates to a real MCPToolAdapter.
    """

    def __init__(self, server, cfg) -> None:  # noqa: ANN001
        from pydantic import BaseModel, Field
        self._server = server
        self._cfg = cfg
        self._real_adapter: MCPToolAdapter | None = None
        # We don't know the tool name until the server starts; use a
        # generic name + description; the agent will discover the real
        # tools via the message after start. For now expose a single
        # call_tool tool that forwards via the MCP tool name.
        self.name = f"mcp_{cfg.name}"
        self.description = (
            f"MCP server '{cfg.name}' — call_tool(name=..., args=...) "
            f"to invoke any tool on this server. The server starts on first call."
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "MCP tool name"},
                "args": {"type": "object", "description": "Tool arguments as JSON object"},
            },
            "required": ["name"],
        }
        class _Params(BaseModel):
            name: str
            args: dict = Field(default_factory=dict)
        self.parameters_model = _Params

    async def _ensure_started(self) -> None:
        if self._real_adapter is not None:
            return
        await self._server.start()
        # Wrap each real tool as an adapter; then pick by name on each call.
        self._real_adapters: dict[str, MCPToolAdapter] = {}
        for tdef in self._server.tools:
            self._real_adapters[tdef["name"]] = MCPToolAdapter(self._server, tdef)
        self._real_adapter = self  # mark started

    async def execute(self, name: str, args: dict | None = None) -> ToolResult:
        try:
            await self._ensure_started()
        except Exception as e:  # noqa: BLE001
            return ToolResult.fail(f"MCP start failed: {e}", code="mcp_start_error")
        adapter = self._real_adapters.get(name)
        if adapter is None:
            available = ", ".join(self._real_adapters.keys())
            return ToolResult.fail(
                f"MCP tool '{name}' not found (available: {available})",
                code="mcp_tool_not_found",
            )
        return await adapter.execute(**(args or {}))

console = Console()
err_console = Console(stderr=True)
log = get_logger("pure_agent.cli.chat")


def _build_loop(
    *,
    model: str,
    provider_name: str,
    api_key: str,
    project_root: Path,
    brave_api_key: str | None,
    tavily_api_key: str | None = None,
    system_prompt: str = "",
    skills: list | None = None,
    extra_mcp_cfgs: list | None = None,
) -> AIAgentLoop:
    from pure_agent.tools.filesystem import Sandbox

    if provider_name == "minimax":
        provider = MinimaxAdapter(api_key=api_key, model=model)
    else:
        from pure_agent.model.openai_adapter import OpenAIAdapter

        provider = OpenAIAdapter(api_key=api_key, model=model)

    sbox = Sandbox(root=project_root)
    reg = build_default_registry(
        sandbox=sbox,
        brave_api_key=brave_api_key,
        tavily_api_key=tavily_api_key,
    )
    # Phase 10: register MCP configs (lazy start inside agent loop)
    mcp_servers: list = []  # placeholder, configs go down
    if extra_mcp_cfgs:
        for cfg in extra_mcp_cfgs:
            reg.register(_LazyMCPProxy(MCPServer(cfg), cfg))
    # Always prepend an "execute, don't describe" anchor so the model
    # prefers tool calls over prose descriptions, especially after
    # large skill loads that bias toward analysis-style output.
    exec_anchor = (
        "You are an action-oriented agent. When the user asks you to create, "
        "modify, fix, or analyze a file, you MUST call the appropriate tool "
        "(read_file, write_file, edit_file) with concrete content — not just "
        "describe what you would do. Only respond in plain text for pure Q&A "
        "or planning questions.\n\n"
    )
    system_prompt = exec_anchor + (system_prompt or "")
    # Phase 10: append skills to system prompt
    if skills:
        from pure_agent.skills import render_skills_prompt
        skills_text = render_skills_prompt(skills)
        if skills_text:
            system_prompt = (system_prompt + "\n\n" + skills_text) if system_prompt else skills_text
    # Phase 11: auto-recall semantic memory facts into system prompt
    try:
        from pure_agent.config import get_home
        import sqlite3
        db_path = get_home() / "memory.db"
        if not db_path.exists():
            pass  # no memory yet
        else:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "SELECT fact FROM memory_semantic WHERE project_id='default' "
                "ORDER BY confidence DESC, created_at DESC LIMIT 5"
            )
            facts = [r[0] for r in cur.fetchall()]
            conn.close()
            if facts:
                fact_text = "\n".join(f"- {f[:200]}" for f in facts)
                memory_section = f"## Recalled Memory (from past sessions / synced from other agents)\n{fact_text}"
                system_prompt = (system_prompt + "\n\n" + memory_section) if system_prompt else memory_section
    except Exception:
        pass  # best-effort, never crash chat on memory recall failure
    return AIAgentLoop(
        provider=provider,
        tools=reg,
        model=model,
        system_prompt=system_prompt,
    )


def _render_event(event_type: str, payload: dict) -> None:
    """Stream LLM text to stderr in real time."""
    if event_type == "text_delta":
        sys.stderr.write(payload.get("text", ""))
        sys.stderr.flush()
    elif event_type == "tool_call_start":
        tu = payload.get("call")
        if tu:
            err_console.print(
                f"\n[dim cyan]→ tool:[/dim cyan] [bold]{tu.name}[/bold] "
                f"[dim]{json_dumps_short(tu.arguments)}[/dim]"
            )
    elif event_type == "tool_call_end":
        result = payload.get("result")
        ok = payload.get("result_ok", False)
        marker = "[green]✓[/green]" if ok else "[red]✗[/red]"
        if result is not None:
            content = result.to_content()
            preview = content[:300] + ("…" if len(content) > 300 else "")
            err_console.print(f"  {marker} {preview}")


def json_dumps_short(obj: object, max_len: int = 120) -> str:
    import json

    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return s if len(s) <= max_len else s[:max_len] + "…"


async def _run_one(
    prompt: str,
    *,
    model: str,
    provider_name: str,
    api_key: str,
    project_root: Path,
    brave_api_key: str | None,
    tavily_api_key: str | None,
    max_turns: int,
    show_usage: bool,
    skills: list | None = None,
    extra_mcp_servers: list | None = None,
) -> int:
    loop = _build_loop(
        model=model,
        provider_name=provider_name,
        api_key=api_key,
        project_root=project_root,
        brave_api_key=brave_api_key,
        tavily_api_key=tavily_api_key,
        skills=skills,
        extra_mcp_cfgs=extra_mcp_servers,
    )

    def on_event(t: str, p: dict) -> None:
        _render_event(t, p)

    loop._on_event = on_event  # noqa: SLF001

    result = await loop.run(prompt, max_turns=max_turns)

    err_console.print()  # newline after streamed text
    if result.stopped_reason.value == "completed":
        err_console.print(
            Panel(
                Markdown(result.final_text or "(no response)"),
                title="[bold green]assistant[/bold green]",
                border_style="green",
            )
        )
    elif result.stopped_reason.value == "aborted":
        err_console.print("[yellow]aborted[/yellow]")
    elif result.stopped_reason.value == "error":
        err_console.print(f"[red]error:[/red] {result.error}")
    elif result.stopped_reason.value == "circuit_breaker":
        err_console.print(f"[red]circuit breaker:[/red] {result.error}")
    else:
        err_console.print(
            f"[yellow]stopped:[/yellow] {result.stopped_reason.value} "
            f"(turns={result.turns})"
        )

    if show_usage:
        u = result.total_usage
        err_console.print(
            f"[dim]usage: prompt={u.prompt_tokens} completion={u.completion_tokens} "
            f"total={u.total_tokens} | turns={result.turns}[/dim]"
        )

    return 0 if result.stopped_reason.value in {"completed", "max_turns"} else 1


def chat(
    prompt: Optional[str] = typer.Argument(
        None,
        help="Single prompt to send. If omitted, enters REPL.",
    ),
    model: str = typer.Option(
        "MiniMax-M3",
        "--model",
        "-m",
        help="Model name (default: MiniMax-M3).",
    ),
    provider: str = typer.Option(
        "minimax",
        "--provider",
        "-p",
        help="Provider name (default: minimax).",
    ),
    project_root: Optional[Path] = typer.Option(
        None,
        "--project-root",
        help="Project root for sandbox (default: $PURE_AGENT_PROJECT_ROOT or cwd).",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="API key (default: $MINIMAX_API_KEY or $OPENAI_API_KEY).",
    ),
    max_turns: int = typer.Option(30, "--max-turns", help="Max agent turns."),
    no_usage: bool = typer.Option(False, "--no-usage", help="Hide token usage."),
    repl: bool = typer.Option(False, "--repl", help="Force REPL even if prompt given."),
) -> None:
    """Chat with the agent. Use --no-args for REPL or pass a prompt."""
    cfg = load_config()
    setup_logging(level=cfg.logging.level, log_file=cfg.paths.agent_log, json_format=False)

    # resolve API key
    key = api_key or os.environ.get("MINIMAX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        err_console.print(
            "[red]error:[/red] no API key. Set MINIMAX_API_KEY or pass --api-key."
        )
        raise typer.Exit(1)

    brave = os.environ.get("BRAVE_API_KEY")
    tavily = os.environ.get("TAVILY_API_KEY")
    root = project_root or Path(os.environ.get("PURE_AGENT_PROJECT_ROOT") or os.getcwd())

    # Phase 10: auto-load skills + MCP servers
    from pure_agent.skills import discover_skills
    from pure_agent.mcp import load_mcp_config, MCPServer
    import asyncio as _asyncio
    from pure_agent.config import get_home as _get_home

    skills = discover_skills(_get_home() / "skills")
    mcp_cfgs = load_mcp_config(_get_home() / "mcp.json")

    mcp_servers: list = []
    if mcp_cfgs:
        # Phase 10 fix: don't pre-start MCP servers here (the REPL/agent
        # loop runs on a different event loop). Instead, pass configs to
        # _build_loop which starts them in the same loop.
        mcp_servers = mcp_cfgs  # pass configs down, server starts in-loop

    if skills:
        err_console.print(f"[dim]loaded {len(skills)} skill(s): {', '.join(s.name for s in skills)}[/dim]")
    if mcp_servers:
        err_console.print(f"[dim]configured {len(mcp_servers)} MCP server(s) (will start on first use)[/dim]")

    try:
        if prompt is None or repl:
            # REPL
            from pure_agent.cli.repl import run_repl

            run_repl(
                model=model,
                provider_name=provider,
                api_key=key,
                project_root=root,
                brave_api_key=brave,
                tavily_api_key=tavily,
                max_turns=max_turns,
                skills=skills,
                extra_mcp_cfgs=mcp_servers,
            )
        else:
            code = asyncio.run(
                _run_one(
                    prompt,
                    model=model,
                    provider_name=provider,
                    api_key=key,
                    project_root=root,
                    brave_api_key=brave,
                    tavily_api_key=tavily,
                    max_turns=max_turns,
                    show_usage=not no_usage,
                    skills=skills,
                    extra_mcp_cfgs=mcp_servers,
                )
            )
            raise typer.Exit(code)
    finally:
        pass  # MCP servers are managed inside the agent loop


__all__ = ["chat"]
