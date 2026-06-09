"""`pure-agent memory` — memory layer CLI subcommands."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from pure_agent.config import load_config
from pure_agent.logging import get_logger, setup_logging
from pure_agent.memory import (
    EpisodicMemory,
    MemoryLayers,
    ProceduralMemory,
    SemanticMemory,
)
from pure_agent.persistence import Database

console = Console()
err_console = Console(stderr=True)
log = get_logger("pure_agent.cli.memory")

app = typer.Typer(help="Manage the four memory layers (episodic/semantic/procedural/short).")


def _db() -> tuple[Database, MemoryLayers]:
    cfg = load_config()
    setup_logging(level=cfg.logging.level, log_file=cfg.paths.agent_log, json_format=False)
    db = Database(path=cfg.paths.memory_db)
    layers = MemoryLayers(db, session_id="default", project_id="default", user_id="default")
    return db, layers


def memory_show(
    layer: str = typer.Argument("all", help="Layer: episodic | semantic | procedural | all"),
) -> None:
    """Show memory contents."""
    _, layers = _db()
    if layer in ("episodic", "all"):
        facts = layers.episodic.recent(limit=20)
        if facts:
            t = Table(title="Episodic memory (recent)", show_header=True)
            t.add_column("id")
            t.add_column("event")
            t.add_column("importance")
            t.add_column("content")
            for f in facts:
                t.add_row(f["id"], f["event"], f"{f['importance']:.2f}", str(f["content"])[:80])
            console.print(t)
    if layer in ("semantic", "all"):
        facts = layers.semantic.all_facts()
        if facts:
            t = Table(title="Semantic memory (project facts)", show_header=True)
            t.add_column("fact")
            t.add_column("confidence")
            t.add_column("source")
            for f in facts:
                t.add_row(f["fact"], f"{f['confidence']:.2f}", f["source"] or "")
            console.print(t)
    if layer in ("procedural", "all"):
        prefs = layers.procedural.all_prefs()
        if prefs:
            t = Table(title="Procedural memory (user preferences)", show_header=True)
            t.add_column("kind")
            t.add_column("content")
            t.add_column("weight")
            for p in prefs:
                t.add_row(p["kind"], p["content"], f"{p['weight']:.2f}")
            console.print(t)
    if layer not in ("episodic", "semantic", "procedural", "all"):
        err_console.print(f"[red]unknown layer:[/red] {layer}")
        raise typer.Exit(1)


def memory_add_fact(
    fact: str = typer.Argument(..., help="Project fact text"),
    source: Optional[str] = typer.Option(None, "--source"),
    confidence: float = typer.Option(1.0, "--confidence", min=0.0, max=1.0),
) -> None:
    """Add a semantic memory fact."""
    _, layers = _db()
    fid = layers.semantic.add(fact, source=source, confidence=confidence)
    err_console.print(f"[green]added[/green] {fid}")


def memory_add_pref(
    kind: str = typer.Argument(..., help="Pref kind, e.g. 'language' or 'tool'"),
    content: str = typer.Argument(..., help="Preference content"),
    weight: float = typer.Option(1.0, "--weight", min=0.0, max=2.0),
) -> None:
    """Add a procedural memory (user preference)."""
    _, layers = _db()
    pid = layers.procedural.add(kind, content, weight=weight)
    err_console.print(f"[green]added[/green] {pid}")


def memory_prompt() -> None:
    """Show what would be injected into the system prompt from all memory layers."""
    _, layers = _db()
    section = layers.as_prompt_sections()
    if not section:
        err_console.print("[dim]no memory contents[/dim]")
        return
    console.print(section)


__all__ = ["app", "memory_show", "memory_add_fact", "memory_add_pref", "memory_prompt"]
