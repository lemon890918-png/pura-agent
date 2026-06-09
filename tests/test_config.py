"""Tests for config: paths, load, env override, defaults."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.smoke
def test_home_resolves_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PURE_AGENT_HOME", str(tmp_path))
    from pure_agent.config import get_home

    assert get_home() == tmp_path.resolve()


@pytest.mark.smoke
def test_default_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PURE_AGENT_HOME", raising=False)
    from pure_agent.config import get_home, DEFAULT_HOME

    assert get_home() == DEFAULT_HOME.resolve()


@pytest.mark.smoke
def test_load_config_creates_paths(tmp_home: Path) -> None:
    from pure_agent.config import load_config

    cfg = load_config(home=tmp_home)
    assert cfg.paths.home == tmp_home
    assert cfg.paths.config_file.exists()
    assert cfg.paths.projects_dir.is_dir()
    assert cfg.paths.logs_dir.is_dir()
    assert cfg.paths.cache_dir.is_dir()


@pytest.mark.smoke
def test_load_config_defaults(tmp_home: Path) -> None:
    from pure_agent.config import load_config

    cfg = load_config(home=tmp_home)
    assert cfg.agent.max_iterations == 90
    assert cfg.agent.token_budget_per_step == 50_000
    assert cfg.server.port == 18789
    assert cfg.logging.level == "INFO"


@pytest.mark.smoke
def test_env_override_log_level(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PURE_AGENT_LOG_LEVEL", "DEBUG")
    from pure_agent.config import load_config

    cfg = load_config(home=tmp_home)
    assert cfg.logging.level == "DEBUG"


@pytest.mark.smoke
def test_env_override_server_port(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PURE_AGENT_SERVER_PORT", "19999")
    from pure_agent.config import load_config

    cfg = load_config(home=tmp_home)
    assert cfg.server.port == 19999


@pytest.mark.smoke
def test_write_default_config_idempotent(tmp_home: Path) -> None:
    from pure_agent.config import write_default_config

    p1 = write_default_config(home=tmp_home)
    original = p1.read_text()
    p2 = write_default_config(home=tmp_home)
    assert p1 == p2
    assert p2.read_text() == original


@pytest.mark.smoke
def test_write_default_config_force(tmp_home: Path) -> None:
    from pure_agent.config import write_default_config

    p1 = write_default_config(home=tmp_home)
    p1.write_text("agent:\n  max_iterations: 999\n")
    p2 = write_default_config(home=tmp_home, force=True)
    assert "999" not in p2.read_text()  # overwritten with defaults
