"""Smoke tests for Phase 0: import, version, basic wiring."""

from __future__ import annotations

import subprocess
import sys

import pytest
from typer.testing import CliRunner

from pure_agent import __version__
from pure_agent.cli.main import app

runner = CliRunner()


@pytest.mark.smoke
def test_version_constant() -> None:
    assert __version__ == "0.1.0"


@pytest.mark.smoke
def test_import_paths() -> None:
    """All top-level modules import without error."""
    import pure_agent
    import pure_agent.agent
    import pure_agent.cli
    import pure_agent.config
    import pure_agent.harness
    import pure_agent.logging
    import pure_agent.memory
    import pure_agent.model
    import pure_agent.persistence
    import pure_agent.plan
    import pure_agent.server
    import pure_agent.tools

    assert pure_agent.__version__


@pytest.mark.smoke
def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


@pytest.mark.smoke
def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "pure-agent" in result.stdout.lower()


@pytest.mark.smoke
def test_cli_init_creates_home(tmp_home) -> None:
    """init creates the home structure and config.yaml."""
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stdout + result.stderr

    assert (tmp_home / "config.yaml").exists()
    assert (tmp_home / "memory.db").exists()
    assert (tmp_home / "logs").is_dir()
    assert (tmp_home / "projects").is_dir()
    assert (tmp_home / "cache").is_dir()


@pytest.mark.smoke
def test_cli_init_idempotent(tmp_home) -> None:
    """Running init twice doesn't clobber existing config."""
    runner.invoke(app, ["init"])
    cfg = tmp_home / "config.yaml"
    original = cfg.read_text()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert cfg.read_text() == original


@pytest.mark.smoke
def test_cli_status(tmp_home) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "schema_version" in result.stdout or "tables" in result.stdout
