"""Configuration: YAML + env, with ~/.pure-agent/ paths."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv


# ---------- paths ----------

HOME_ENV = "PURE_AGENT_HOME"
DEFAULT_HOME = Path.home() / ".pure-agent"


def get_home() -> Path:
    """Resolve the pure-agent home directory.

    Order: PURE_AGENT_HOME env > ~/.pure-agent/

    Note: returns the path with symlinks resolved (.resolve()). On macOS,
    /var/folders is a symlink to /private/var/folders, so callers that
    compare against a non-resolved path should resolve on their side too.
    """
    h = os.environ.get(HOME_ENV)
    if h:
        return Path(h).expanduser().resolve()
    return DEFAULT_HOME.resolve()


def get_home_or_none() -> Path | None:
    """Return home only if it exists."""
    h = get_home()
    return h if h.exists() else None


# ---------- data classes ----------


@dataclass(frozen=True)
class AgentConfig:
    max_iterations: int = 90
    max_output_tokens: int = 16384
    token_budget_per_step: int = 50_000
    tool_timeout_seconds: int = 120
    llm_timeout_seconds: int = 300
    step_timeout_seconds: int = 1800
    checkpoint_every_n_tool_calls: int = 5


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 18789
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://localhost:3001")


@dataclass(frozen=True)
class LoggingConfig:
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_format: bool = True
    file: str = "logs/agent.log"


@dataclass(frozen=True)
class Paths:
    home: Path
    config_file: Path
    env_file: Path
    projects_dir: Path
    logs_dir: Path
    cache_dir: Path
    memory_db: Path
    agent_log: Path

    def ensure(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            self.config_file.touch()
        if not self.env_file.exists():
            self.env_file.touch()


@dataclass(frozen=True)
class Config:
    home: Path
    paths: Paths
    agent: AgentConfig = field(default_factory=AgentConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def version(self) -> str:
        from pure_agent import __version__
        return __version__


# ---------- defaults ----------


_DEFAULT_CONFIG: dict[str, Any] = {
    "agent": {
        "max_iterations": 90,
        "max_output_tokens": 16384,
        "token_budget_per_step": 50_000,
        "tool_timeout_seconds": 120,
        "llm_timeout_seconds": 300,
        "step_timeout_seconds": 1800,
        "checkpoint_every_n_tool_calls": 5,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 18789,
        "cors_origins": ["http://localhost:5173", "http://localhost:3001"],
    },
    "logging": {
        "level": "INFO",
        "json_format": True,
        "file": "logs/agent.log",
    },
    "providers": {},
}


# ---------- loader ----------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base, override wins."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _filter_known_fields(d: dict[str, Any], cls: type) -> dict[str, Any]:
    """Filter a dict to only keys that are valid init params of `cls`.

    Prevents TypeError when config.yaml has stale/extra keys (e.g. when
    syncing from another agent's config that uses different field names).
    Unknown keys are silently dropped — they live on in cfg.raw if needed.
    """
    import dataclasses
    if dataclasses.is_dataclass(cls):
        valid = {f.name for f in dataclasses.fields(cls)}
    else:
        # Fallback: use __init__ signature
        import inspect
        try:
            valid = set(inspect.signature(cls).parameters.keys())
        except Exception:
            return d
    return {k: v for k, v in d.items() if k in valid}


def _build_paths(home: Path) -> Paths:
    return Paths(
        home=home,
        config_file=home / "config.yaml",
        env_file=home / ".env",
        projects_dir=home / "projects",
        logs_dir=home / "logs",
        cache_dir=home / "cache",
        memory_db=home / "memory.db",
        agent_log=home / "logs" / "agent.log",
    )


def load_config(home: Path | None = None) -> Config:
    """Load configuration from disk.

    Precedence (lowest first):
      defaults < config.yaml < PURE_AGENT_* env
    """
    h = home or get_home()
    paths = _build_paths(h)
    paths.ensure()

    # env file in home
    if paths.env_file.exists():
        load_dotenv(paths.env_file, override=False)

    raw: dict[str, Any] = {}
    if paths.config_file.exists() and paths.config_file.stat().st_size > 0:
        with paths.config_file.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"config.yaml must be a dict, got {type(loaded).__name__}")
        raw = loaded

    merged = _deep_merge(_DEFAULT_CONFIG, raw)

    # env override for selected fields
    if v := os.environ.get("PURE_AGENT_LOG_LEVEL"):
        merged["logging"]["level"] = v.upper()
    if v := os.environ.get("PURE_AGENT_SERVER_PORT"):
        try:
            merged["server"]["port"] = int(v)
        except ValueError:
            pass

    agent = AgentConfig(**_filter_known_fields(merged["agent"], AgentConfig))
    server = ServerConfig(
        host=merged["server"]["host"],
        port=merged["server"]["port"],
        cors_origins=tuple(merged["server"].get("cors_origins", [])),
    )
    logging_cfg = LoggingConfig(**merged["logging"])

    return Config(
        home=h,
        paths=paths,
        agent=agent,
        server=server,
        logging=logging_cfg,
        raw=merged,
    )


def write_default_config(home: Path | None = None, force: bool = False) -> Path:
    """Write a starter config.yaml. Idempotent unless force=True."""
    h = home or get_home()
    paths = _build_paths(h)
    paths.ensure()
    if paths.config_file.exists() and paths.config_file.stat().st_size > 0 and not force:
        return paths.config_file

    starter = {
        "agent": {
            "max_iterations": 90,
            "max_output_tokens": 16384,
            "token_budget_per_step": 50_000,
            "tool_timeout_seconds": 120,
            "llm_timeout_seconds": 300,
            "step_timeout_seconds": 1800,
            "checkpoint_every_n_tool_calls": 5,
        },
        "server": {
            "host": "127.0.0.1",
            "port": 18789,
            "cors_origins": ["http://localhost:5173", "http://localhost:3001"],
        },
        "logging": {
            "level": "INFO",
            "json_format": True,
            "file": "logs/agent.log",
        },
        "providers": {
            "minimax": {
                "protocol": "openai",
                "url": "https://api.minimaxi.com/v1",
                "api_key_env": "MINIMAX_API_KEY",
                "default_model": "MiniMax-M3",
                "fallback_models": ["MiniMax-M2.7"],
            },
        },
    }
    with paths.config_file.open("w", encoding="utf-8") as f:
        yaml.safe_dump(starter, f, allow_unicode=True, sort_keys=False)
    return paths.config_file
