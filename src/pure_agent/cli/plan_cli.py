"""`pure-agent plan` — long-running task CLI subcommands."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from pure_agent.config import get_home, load_config
from pure_agent.logging import get_logger, setup_logging
from pure_agent.persistence import Database
from pure_agent.plan import (
    Goal,
    GoalStatus,
    Plan,
    PlanAgent,
    PlanRunner,
    PlanStatus,
    PlanStep,
    PlanStorage,
    StepKind,
    StepStatus,
    StepReport,
)
from pure_agent.tools.filesystem import Sandbox

console = Console()
err_console = Console(stderr=True)
log = get_logger("pure_agent.cli.plan")


def _build_runner(
    *,
    model: str,
    provider_name: str,
    api_key: str,
    project_root: Path,
    brave_api_key: str | None,
) -> PlanRunner:
    """Build PlanRunner with the same loop factory as chat."""
    from pure_agent.cli.chat import _build_loop

    def loop_factory(*, system_prompt: str = ""):
        return _build_loop(
            model=model,
            provider_name=provider_name,
            api_key=api_key,
            project_root=project_root,
            brave_api_key=brave_api_key,
            system_prompt=system_prompt,
        )

    db = Database(path=load_config().paths.memory_db)
    storage = PlanStorage(db)
    return PlanRunner(storage, loop_factory)


def _print_plan_tree(plan: Plan) -> None:
    tree = Tree(f"[bold]plan {plan.id}[/bold] (v{plan.version}, {plan.status.value})")
    for s in plan.steps:
        marker = {
            StepStatus.DONE: "[green]✓[/green]",
            StepStatus.FAILED: "[red]✗[/red]",
            StepStatus.IN_PROGRESS: "[yellow]…[/yellow]",
            StepStatus.BLOCKED: "[red]⊘[/red]",
            StepStatus.SKIPPED: "[dim]⊝[/dim]",
            StepStatus.PENDING: "[dim]·[/dim]",
        }.get(s.status, "?")
        label = f"{marker} {s.id} [{s.kind.value}] {s.action}"
        if s.deps:
            label += f"  [dim](deps: {', '.join(s.deps)})[/dim]"
        if s.step_report:
            label += f"\n   [dim]verdict={s.step_report.verdict}[/dim]"
            label += f"\n   [dim]summary: {s.step_report.summary[:120]}[/dim]"
        if s.last_error and s.status == StepStatus.FAILED:
            label += f"\n   [red]error: {s.last_error[:120]}[/red]"
        tree.add(label)
    console.print(tree)


def plan(
    goal: Optional[str] = typer.Argument(
        None,
        help="Goal text. If omitted, opens interactive plan wizard.",
    ),
    project_id: str = typer.Option(
        "default",
        "--project",
        "-p",
        help="Project id (groups related goals).",
    ),
    model: str = typer.Option("MiniMax-M3", "--model", "-m"),
    provider: str = typer.Option("minimax", "--provider"),
    project_root: Optional[Path] = typer.Option(None, "--project-root"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
    max_turns: int = typer.Option(200, "--max-turns"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip plan confirmation"),
) -> None:
    """Create a Goal, decompose into a Plan, then execute it."""
    cfg = load_config()
    setup_logging(level=cfg.logging.level, log_file=cfg.paths.agent_log, json_format=False)
    if goal is None:
        err_console.print("[red]error:[/red] goal text required")
        raise typer.Exit(1)

    key = api_key or os.environ.get("MINIMAX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        err_console.print("[red]error:[/red] no API key")
        raise typer.Exit(1)

    brave = os.environ.get("BRAVE_API_KEY")
    root = project_root or Path(os.environ.get("PURE_AGENT_PROJECT_ROOT") or os.getcwd())

    db = Database(path=cfg.paths.memory_db)
    storage = PlanStorage(db)

    # create goal
    g = storage.create_goal(project_id=project_id, text=goal)
    err_console.print(f"[dim]goal:[/dim] {g.id}")

    # decompose
    from pure_agent.model import MinimaxAdapter

    if provider == "minimax":
        llm = MinimaxAdapter(api_key=key, model=model)
    else:
        from pure_agent.model import OpenAIAdapter

        llm = OpenAIAdapter(api_key=key, model=model)

    agent = PlanAgent(llm, model)
    err_console.print("[dim]decomposing into plan…[/dim]")
    try:
        plan_obj, _usage = asyncio.run(agent.decompose(g))
    except Exception as e:  # noqa: BLE001
        err_console.print(f"[red]decompose failed:[/red] {e}")
        storage.update_goal_status(g.id, GoalStatus.FAILED)
        raise typer.Exit(1) from e

    plan_obj.goal_id = g.id
    for s in plan_obj.steps:
        s.plan_id = plan_obj.id
    storage.create_plan(plan_obj)

    # show plan + ask confirm
    _print_plan_tree(plan_obj)
    if not yes:
        confirm = typer.confirm("Execute this plan?", default=True)
        if not confirm:
            err_console.print("[yellow]aborted[/yellow]")
            raise typer.Exit(0)

    # run
    storage.update_goal_status(g.id, GoalStatus.RUNNING)
    runner = _build_runner(
        model=model,
        provider_name=provider,
        api_key=key,
        project_root=root,
        brave_api_key=brave,
    )

    def on_event(t: str, p: dict) -> None:
        if t == "step_start":
            err_console.print(f"\n[bold cyan]→[/bold cyan] step {p.get('step_id')}: {p.get('action')}")
        elif t == "step_end":
            err_console.print(f"  [dim]{p.get('status')}[/dim]")
        elif t == "step_blocked":
            err_console.print(f"  [red]blocked:[/red] {p.get('reason')}")

    async def go() -> object:
        return await runner.execute(plan_obj.id, max_total_turns=max_turns)

    result = asyncio.run(go())
    storage.update_goal_status(
        g.id,
        GoalStatus.DONE if result.ok else GoalStatus.FAILED,
    )

    err_console.print()
    if result.ok:
        err_console.print(
            Panel(
                f"[green]plan completed[/green]\n"
                f"  steps: {result.steps_completed} done / {result.steps_failed} failed\n"
                f"  tokens: {result.total_usage.total_tokens} "
                f"(prompt={result.total_usage.prompt_tokens}, completion={result.total_usage.completion_tokens})",
                border_style="green",
            )
        )
    else:
        err_console.print(
            Panel(
                f"[red]plan failed[/red]\n  {result.error}",
                border_style="red",
            )
        )

    # re-show tree
    final = storage.get_plan(plan_obj.id)
    if final:
        _print_plan_tree(final)


def plan_resume(
    plan_id: str = typer.Argument(..., help="Plan id to resume"),
) -> None:
    """Resume a paused/aborted plan."""
    cfg = load_config()
    setup_logging(level=cfg.logging.level, log_file=cfg.paths.agent_log, json_format=False)
    err_console.print(f"[dim]resuming plan {plan_id}…[/dim]")

    # use env for model/keys
    key = os.environ.get("MINIMAX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        err_console.print("[red]error:[/red] no API key in env")
        raise typer.Exit(1)
    brave = os.environ.get("BRAVE_API_KEY")
    root = Path(os.environ.get("PURE_AGENT_PROJECT_ROOT") or os.getcwd())

    runner = _build_runner(
        model=os.environ.get("PURE_AGENT_MODEL", "MiniMax-M3"),
        provider_name="minimax",
        api_key=key,
        project_root=root,
        brave_api_key=brave,
    )

    async def go() -> object:
        return await runner.resume(plan_id)

    result = asyncio.run(go())
    err_console.print(f"final status: {result.final_status.value} | {result.error or 'ok'}")


def plan_list(
    goal_id: Optional[str] = typer.Option(None, "--goal"),
) -> None:
    """List all plans."""
    cfg = load_config()
    setup_logging(level=cfg.logging.level, log_file=cfg.paths.agent_log, json_format=False)
    db = Database(path=cfg.paths.memory_db)
    storage = PlanStorage(db)
    plans = storage.list_plans(goal_id=goal_id)
    if not plans:
        err_console.print("[dim]no plans[/dim]")
        return
    table = Table(title="plans", show_header=True, header_style="bold")
    table.add_column("id")
    table.add_column("goal_id")
    table.add_column("status")
    table.add_column("v")
    table.add_column("steps")
    table.add_column("created")
    for p in plans:
        table.add_row(
            p.id,
            p.goal_id,
            p.status.value,
            str(p.version),
            f"{sum(1 for s in p.steps if s.status == StepStatus.DONE)}/{len(p.steps)}",
            p.created_at[:19],
        )
    console.print(table)


def plan_show(
    plan_id: str = typer.Argument(...),
) -> None:
    """Show full plan details including step tree."""
    cfg = load_config()
    setup_logging(level=cfg.logging.level, log_file=cfg.paths.agent_log, json_format=False)
    db = Database(path=cfg.paths.memory_db)
    storage = PlanStorage(db)
    p = storage.get_plan(plan_id)
    if p is None:
        err_console.print(f"[red]plan not found:[/red] {plan_id}")
        raise typer.Exit(1)
    _print_plan_tree(p)


__all__ = ["plan", "plan_resume", "plan_list", "plan_show"]
