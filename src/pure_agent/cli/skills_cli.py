"""`pure-agent skills` — manage local skills (Phase 10)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from pure_agent.config import get_home
from pure_agent.skills import (
    discover_skills,
    load_skill,
    render_skills_prompt,
)

skills_app = typer.Typer(help="Manage agent skills (loaded from ~/.pure-agent/skills).")
console = Console()


def _skills_dir() -> Path:
    p = get_home() / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


@skills_app.command("list")
def list_cmd() -> None:
    """List installed skills."""
    sd = _skills_dir()
    skills = discover_skills(sd)
    if not skills:
        console.print(f"[dim]no skills installed. Skills dir: {sd}[/dim]")
        raise typer.Exit(0)
    for s in skills:
        console.print(
            f"[bold cyan]{s.name}[/bold cyan]  v{s.version}  "
            f"[dim]({s.source})[/dim]"
        )
        if s.description:
            console.print(f"   {s.description}")
        if s.allowed_tools:
            console.print(f"   [dim]tools: {', '.join(s.allowed_tools)}[/dim]")


@skills_app.command("show")
def show_cmd(name: str = typer.Argument(..., help="Skill name to show")) -> None:
    """Show a skill's full content."""
    sd = _skills_dir()
    skill_dir = sd / name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        console.print(f"[red]skill '{name}' not found in {sd}[/red]")
        raise typer.Exit(1)
    s = load_skill(skill_md)
    console.print(Panel(render_skills_prompt([s]), title=f"[bold]{s.name}[/bold]"))


@skills_app.command("add")
def add_cmd(
    source: str = typer.Argument(..., help="owner/repo to install from GitHub"),
    name: str | None = typer.Option(None, "--name", help="Override skill name"),
    branch: str = typer.Option("main", "--branch", help="Git branch"),
) -> None:
    """Install a skill from a GitHub repo (clones the repo, then copies the skill dir).

    Examples:
      pure-agent skills add tavily-ai/skills --name tavily-search
      pure-agent skills add mattpocock/skills --name code-review
    """
    if "/" not in source:
        console.print("[red]source must be 'owner/repo' (e.g. tavily-ai/skills)[/red]")
        raise typer.Exit(1)
    owner, repo = source.split("/", 1)
    sd = _skills_dir()
    # if name is given, the directory is just `name`; otherwise use repo
    target_name = name or repo
    target_dir = sd / target_name
    if target_dir.exists():
        console.print(f"[yellow]skill '{target_name}' already installed.[/yellow]")
        raise typer.Exit(0)
    # clone
    clone_url = f"https://github.com/{owner}/{repo}.git"
    console.print(f"[dim]cloning {clone_url} (branch={branch})...[/dim]")
    tmp_dir = sd / f".tmp_{target_name}"
    try:
        subprocess.run(
            [
                "git", "clone", "--depth", "1", "--branch", branch,
                clone_url, str(tmp_dir),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[red]git clone failed: {e.stderr.decode()[:500]}[/red]")
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print("[red]git is not installed[/red]")
        raise typer.Exit(1)
    # find SKILL.md anywhere in the clone
    skill_md = None
    for p in tmp_dir.rglob("SKILL.md"):
        skill_md = p
        break
    if skill_md is None:
        console.print(f"[red]no SKILL.md found in {clone_url}[/red]")
        shutil.rmtree(tmp_dir)
        raise typer.Exit(1)
    # move the parent dir
    shutil.move(str(skill_md.parent), str(target_dir))
    shutil.rmtree(tmp_dir)
    console.print(f"[green]installed '{target_name}' to {target_dir}[/green]")
    console.print(f"[dim]run `pure-agent skills show {target_name}` to see content[/dim]")


@skills_app.command("remove")
def remove_cmd(name: str = typer.Argument(..., help="Skill name to remove")) -> None:
    """Remove an installed skill."""
    sd = _skills_dir()
    target = sd / name
    if not target.exists():
        console.print(f"[red]skill '{name}' not found[/red]")
        raise typer.Exit(1)
    shutil.rmtree(target)
    console.print(f"[green]removed '{name}'[/green]")


@skills_app.command("path")
def path_cmd() -> None:
    """Print the skills directory path."""
    console.print(str(_skills_dir()))
