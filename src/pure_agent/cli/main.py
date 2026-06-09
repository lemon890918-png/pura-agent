"""CLI entry point (typer app)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from pure_agent import __version__
from pure_agent.config import (
    get_home,
    load_config,
    write_default_config,
)
from pure_agent.logging import setup_logging
from pure_agent.persistence import Database

app = typer.Typer(
    name="pure-agent",
    help="Pure self-built agent runtime with Goal/Plan long-running support.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"pure-agent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """pure-agent CLI."""


@app.command()
def init(
    home: Optional[Path] = typer.Option(
        None,
        "--home",
        help="Override home directory (default: ~/.pure-agent or $PURE_AGENT_HOME).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing config.yaml if present.",
    ),
) -> None:
    """Initialize ~/.pure-agent/ with config.yaml and SQLite schema."""
    if home is not None:
        import os

        os.environ["PURE_AGENT_HOME"] = str(home.expanduser().resolve())

    h = get_home()
    console.print(f"[bold]Home:[/bold] {h}")

    config_path = write_default_config(home=h, force=force)
    console.print(f"[green]✓[/green] config: {config_path}")

    cfg = load_config(home=h)
    cfg.paths.ensure()

    db = Database(path=cfg.paths.memory_db)
    version = db.schema_version()
    tables = db.table_names()
    db.close()
    console.print(f"[green]✓[/green] memory.db (schema v{version}, {len(tables)} tables)")

    log_file = cfg.paths.agent_log
    console.print(f"[green]✓[/green] log file: {log_file}")

    setup_logging(level=cfg.logging.level, log_file=log_file, json_format=cfg.logging.json_format)
    from pure_agent.logging import get_logger

    log = get_logger("pure_agent.cli.init")
    log.info("initialized", home=str(h), tables=len(tables))

    console.print(f"\n[bold green]Done.[/bold green] Run [cyan]pure-agent --help[/cyan] for next steps.")


@app.command()
def status() -> None:
    """Show current configuration and persistence state."""
    cfg = load_config()
    cfg.paths.ensure()

    setup_logging(level=cfg.logging.level, log_file=cfg.paths.agent_log, json_format=cfg.logging.json_format)

    table = Table(title=f"pure-agent {__version__}", show_header=True, header_style="bold")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")

    table.add_row("home", str(cfg.home))
    table.add_row("config", str(cfg.paths.config_file))
    table.add_row("memory.db", str(cfg.paths.memory_db))
    table.add_row("server", f"{cfg.server.host}:{cfg.server.port}")
    table.add_row("log_level", cfg.logging.level)
    table.add_row("agent.max_iterations", str(cfg.agent.max_iterations))
    table.add_row("agent.token_budget_per_step", str(cfg.agent.token_budget_per_step))

    db = Database(path=cfg.paths.memory_db)
    table.add_row("schema_version", str(db.schema_version()))
    table.add_row("tables", ", ".join(db.table_names()))
    db.close()

    console.print(table)


# Import chat command (Phase 1)
try:
    from pure_agent.cli.chat import chat as chat_cmd

    app.command(name="chat")(chat_cmd)
except ImportError:
    pass

# Import plan commands (Phase 2)
# Import memory CLI (Phase 3)
try:
    from pure_agent.cli.memory_cli import memory_app
    app.add_typer(memory_app, name="memory")
except ImportError:
    pass

# Import serve CLI (Phase 7)
try:
    from pure_agent.cli.serve_cli import serve_app
    app.add_typer(serve_app, name="serve")
except ImportError:
    pass

# Import UI CLI (Phase 8)
try:
    from pure_agent.cli.ui_cli import ui_app
    app.add_typer(ui_app, name="ui")
except ImportError:
    pass

# Import bench CLI (Phase 9)
try:
    from pure_agent.cli.bench_cli import bench_app
    app.add_typer(bench_app, name="bench")
except ImportError:
    pass

# Import skills CLI (Phase 10)
try:
    from pure_agent.cli.skills_cli import skills_app
    app.add_typer(skills_app, name="skills")
except ImportError:
    pass

# Import MCP CLI (Phase 10)
try:
    from pure_agent.cli.mcp_cli import mcp_app
    app.add_typer(mcp_app, name="mcp")
except ImportError:
    pass


if __name__ == "__main__":
    app()
