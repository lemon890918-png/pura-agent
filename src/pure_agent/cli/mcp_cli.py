"""`pure-agent mcp` — manage MCP servers (Phase 10)."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pure_agent.config import get_home
from pure_agent.mcp import (
    MCPServer,
    MCPServerConfig,
    load_mcp_config,
)

mcp_app = typer.Typer(help="Manage MCP (Model Context Protocol) servers.")
console = Console()


def _mcp_config_path() -> Path:
    return get_home() / "mcp.json"


@mcp_app.command("list")
def list_cmd() -> None:
    """List configured MCP servers."""
    p = _mcp_config_path()
    if not p.exists():
        console.print(f"[dim]no mcp.json at {p}[/dim]")
        raise typer.Exit(0)
    cfgs = load_mcp_config(p)
    if not cfgs:
        console.print("[dim]no servers configured[/dim]")
        raise typer.Exit(0)
    table = Table(title="MCP servers")
    table.add_column("name", style="cyan")
    table.add_column("command")
    table.add_column("args")
    for c in cfgs:
        table.add_row(c.name, c.command, " ".join(c.args))
    console.print(table)


@mcp_app.command("add")
def add_cmd(
    name: str = typer.Argument(..., help="Server name (e.g. filesystem)"),
    command: str = typer.Argument(..., help="Command to run (e.g. npx)"),
) -> None:
    """Add an MCP server. Subsequent args are forwarded.

    Example:
      pure-agent mcp add filesystem npx -y @modelcontextprotocol/server-filesystem /tmp
    """
    import sys as _sys
    args = _sys.argv[_sys.argv.index("add") + 3:]  # everything after the command
    p = _mcp_config_path()
    data: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            data = {}
    data.setdefault("mcpServers", {})[name] = {
        "command": command,
        "args": args,
        "env": {},
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    console.print(f"[green]added MCP server '{name}' to {p}[/green]")


@mcp_app.command("remove")
def remove_cmd(name: str = typer.Argument(..., help="Server name to remove")) -> None:
    """Remove an MCP server from config."""
    p = _mcp_config_path()
    if not p.exists():
        console.print(f"[red]no config at {p}[/red]")
        raise typer.Exit(1)
    data = json.loads(p.read_text())
    if name not in data.get("mcpServers", {}):
        console.print(f"[red]server '{name}' not in config[/red]")
        raise typer.Exit(1)
    del data["mcpServers"][name]
    p.write_text(json.dumps(data, indent=2))
    console.print(f"[green]removed '{name}'[/green]")


@mcp_app.command("test")
def test_cmd(name: str = typer.Argument(..., help="Server name to test")) -> None:
    """Spawn the server and list its tools (verifies it starts)."""
    p = _mcp_config_path()
    if not p.exists():
        console.print(f"[red]no config at {p}[/red]")
        raise typer.Exit(1)
    cfgs = load_mcp_config(p)
    cfg = next((c for c in cfgs if c.name == name), None)
    if cfg is None:
        console.print(f"[red]server '{name}' not in config[/red]")
        raise typer.Exit(1)
    console.print(f"[dim]starting {cfg.command} {' '.join(cfg.args)} ...[/dim]")

    async def go() -> None:
        server = MCPServer(cfg)
        try:
            await server.start()
            tools = server.tools
            console.print(
                f"[green]✓ {name} started. serverInfo: {server.server_info}[/green]"
            )
            console.print(f"[green]✓ {len(tools)} tool(s) available:[/green]")
            for t in tools:
                console.print(f"   - {t.get('name')}: {t.get('description', '')}")
        except Exception as e:
            console.print(f"[red]✗ failed to start: {e}[/red]")
            raise typer.Exit(1)
        finally:
            await server.stop()

    try:
        asyncio.run(go())
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]error: {e}[/red]")
        raise typer.Exit(1)
