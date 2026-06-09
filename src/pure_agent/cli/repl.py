"""REPL for `pure-agent chat` (no-arg mode)."""

from __future__ import annotations

import asyncio
import os

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from pure_agent.agent import AIAgentLoop
from pure_agent.cli.chat import _build_loop, _render_event
from pure_agent.config import get_home
from pure_agent.logging import get_logger

err_console = Console(stderr=True)
console = Console()
log = get_logger("pure_agent.cli.repl")


def _make_session() -> PromptSession:
    history_path = get_home() / "cache" / "chat_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(history=FileHistory(str(history_path)))


async def _repl_loop(
    session: PromptSession,
    *,
    model: str,
    provider_name: str,
    api_key: str,
    project_root,
    brave_api_key: str | None,
    tavily_api_key: str | None = None,
    max_turns: int,
    skills: list | None = None,
    extra_mcp_cfgs: list | None = None,
) -> None:
    loop = _build_loop(
        model=model,
        provider_name=provider_name,
        api_key=api_key,
        project_root=project_root,
        brave_api_key=brave_api_key,
        tavily_api_key=tavily_api_key,
        skills=skills,
        extra_mcp_cfgs=extra_mcp_cfgs,
    )

    err_console.print(
        "[dim]pure-agent REPL — Ctrl-D to exit, /help for commands[/dim]"
    )
    while True:
        try:
            with patch_stdout():
                text = await session.prompt_async("you> ")
        except (EOFError, KeyboardInterrupt):
            err_console.print("\n[dim]bye[/dim]")
            return

        text = text.strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            err_console.print("[dim]bye[/dim]")
            return
        if text == "/help":
            err_console.print(
                "[dim]commands: /help /exit /tools /model /clear[/dim]"
            )
            continue
        if text == "/tools":
            err_console.print(f"[dim]tools: {loop.tools.names()}[/dim]")
            continue
        if text.startswith("/model "):
            loop.model = text.split(maxsplit=1)[1]
            err_console.print(f"[dim]model set to {loop.model}[/dim]")
            continue
        if text == "/clear":
            console.clear()
            continue

        # attach event renderer
        loop._on_event = (  # noqa: SLF001
            lambda t, p: _render_event(t, p)
        )
        err_console.print(f"[dim]assistant>[/dim] ", end="")
        result = await loop.run(text, max_turns=max_turns)
        err_console.print()  # newline
        if result.stopped_reason.value == "completed":
            err_console.print(
                Panel(
                    Markdown(result.final_text or "(no response)"),
                    title="[bold green]final[/bold green]",
                    border_style="green",
                )
            )
        else:
            err_console.print(
                f"[yellow]{result.stopped_reason.value}:[/yellow] {result.error or ''}"
            )
        u = result.total_usage
        err_console.print(
            f"[dim]usage: prompt={u.prompt_tokens} completion={u.completion_tokens} "
            f"total={u.total_tokens} | turns={result.turns}[/dim]"
        )


def run_repl(
    *,
    model: str,
    provider_name: str,
    api_key: str,
    project_root,
    brave_api_key: str | None,
    tavily_api_key: str | None = None,
    max_turns: int,
    skills: list | None = None,
    extra_mcp_cfgs: list | None = None,
) -> None:
    session = _make_session()
    try:
        asyncio.run(
            _repl_loop(
                session,
                model=model,
                provider_name=provider_name,
                api_key=api_key,
                project_root=project_root,
                brave_api_key=brave_api_key,
                tavily_api_key=tavily_api_key,
                max_turns=max_turns,
                skills=skills,
                extra_mcp_cfgs=extra_mcp_cfgs,
            )
        )
    except KeyboardInterrupt:
        err_console.print("\n[dim]bye[/dim]")


__all__ = ["run_repl"]
