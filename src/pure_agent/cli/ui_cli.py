"""`pure-agent ui` — open the web UI in browser (Phase 8)."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

import typer

ui_app = typer.Typer(help="Web UI helpers.")


@ui_app.command("open")
def open_ui(
    port: int = typer.Option(18790, help="gateway port to connect to"),
) -> None:
    """Open the pure-agent web UI in the default browser."""
    # src/pure_agent/cli/ui_cli.py -> src/pure_agent/cli -> src/pure_agent -> src -> project root
    ui_path = Path(__file__).parent.parent.parent.parent / "ui" / "index.html"
    if not ui_path.exists():
        typer.echo(f"UI not found at {ui_path}")
        raise typer.Exit(1)
    # The UI uses a hardcoded port; for production we'd inject via query string.
    # For now, just open the file.
    webbrowser.open(ui_path.as_uri())
    typer.echo(f"opened {ui_path}")
    typer.echo(f"(gateway assumed on port {port}; edit ui/index.html if different)")


@ui_app.command("serve")
def serve_ui(
    port: int = typer.Option(3001, help="local HTTP port"),
    gateway_port: int = typer.Option(18790, help="gateway port to mention"),
) -> None:
    """Serve the UI over a local HTTP port."""
    import http.server
    import socketserver

    ui_dir = Path(__file__).parent.parent.parent.parent / "ui"
    if not (ui_dir / "index.html").exists():
        typer.echo(f"UI not found at {ui_dir}")
        raise typer.Exit(1)

    os.chdir(ui_dir)
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        typer.echo(f"UI on http://127.0.0.1:{port}/  (gateway port {gateway_port})")
        typer.echo("Ctrl-C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            typer.echo("\nstopped")
