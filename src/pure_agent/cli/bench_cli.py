"""`pure-agent bench` — benchmark runner CLI (Phase 9)."""

from __future__ import annotations

import asyncio
import os
import sys

import typer
from rich.console import Console
from rich.table import Table

bench_app = typer.Typer(help="Benchmark pure-agent vs alternatives.")
console = Console()


@bench_app.command("run")
def run(
    project_root: str = typer.Option("/tmp", help="workspace for benchmark files"),
    output: str = typer.Option(None, help="save JSON report to this path"),
    api_key: str = typer.Option(None, help="API key (default: env)"),
) -> None:
    """Run the 5 standard benchmark tasks and report."""
    from pure_agent.benchmark import run_all

    key = api_key or os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        typer.echo("ERROR: no API key. Pass --api-key or set MINIMAX_API_KEY.")
        raise typer.Exit(1)

    report = asyncio.run(
        run_all(
            api_key=key,
            project_root=project_root,
            output_path=output,
        )
    )

    # summary table
    table = Table(title="pure-agent benchmark results")
    table.add_column("task", style="cyan")
    table.add_column("ok", style="green")
    table.add_column("time_s", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("hits", style="green")
    table.add_column("misses", style="red")
    for r in report.get("results", []):
        ok = "✓" if r["success"] else "✗"
        hits = ",".join(r["keyword_hits"]) or "-"
        misses = ",".join(r["keyword_misses"]) or "-"
        table.add_row(
            r["name"],
            ok,
            f"{r['elapsed_s']:.1f}",
            str(r["usage"].get("total_tokens", 0)),
            hits,
            misses,
        )
    console.print(table)
    console.print(
        f"\n{report.get('n_success', 0)}/{report.get('n_tasks', 0)} success · "
        f"{report.get('total_time_s', 0):.1f}s total · "
        f"{report.get('total_tokens', 0)} tokens"
    )


@bench_app.command("setup")
def setup(
    workspace: str = typer.Option("/tmp", help="workspace dir"),
) -> None:
    """Create the benchmark files in the workspace."""
    from pathlib import Path
    from pure_agent.benchmark import setup_files

    setup_files(Path(workspace))
    typer.echo(f"benchmark files written to {workspace}")
