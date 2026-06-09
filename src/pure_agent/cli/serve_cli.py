"""`pure-agent serve` — start the gateway (Phase 7)."""

from __future__ import annotations

import os

import typer

serve_app = typer.Typer(help="Run the pure-agent gateway.")


@serve_app.command("start")
def start(
    host: str = typer.Option("127.0.0.1", help="bind host"),
    port: int = typer.Option(18790, help="gateway port (avoid 18789)"),
    reload: bool = typer.Option(False, help="auto-reload on code changes"),
) -> None:
    """Start the HTTP/WS gateway."""
    import uvicorn

    os.environ["PURE_AGENT_HOST"] = host
    os.environ["PURE_AGENT_PORT"] = str(port)
    typer.echo(f"pure-agent gateway starting on http://{host}:{port}")
    uvicorn.run(
        "pure_agent.server.gateway:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@serve_app.command("info")
def info(
    port: int = typer.Option(18790, help="gateway port to query"),
) -> None:
    """Print gateway info (requires it to be running)."""
    import httpx

    host = os.environ.get("PURE_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("PURE_AGENT_PORT", str(port)))
    url = f"http://{host}:{port}/health"
    try:
        r = httpx.get(url, timeout=5.0)
        typer.echo(r.text)
    except Exception as e:
        typer.echo(f"gateway not reachable: {e}")
        raise typer.Exit(1)
