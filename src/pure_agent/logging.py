"""Logging setup using structlog.

Architecture (Phase 0):
  - Single structlog pipeline.
  - A FanoutLogger routes each event to BOTH stderr (pretty, colored)
    and a file (pretty, newline-delimited).
  - `json_format` is accepted for API stability but ignored (Phase 0
    doesn't need JSON; Phase 4+ will add it via ProcessorFormatter).

structlog 25 API:
  - Factory returns a "sink" with a `msg(message)` method.
  - `log = debug = info = warning = error = critical = msg` (all aliases).
  - We subclass to fan out to multiple targets.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import IO, Literal

import structlog

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class _FanoutLogger:
    """A structlog sink that fans every event out to N file-like targets.

    structlog calls `.msg(text)` once per event. We propagate to all targets.
    The `info/warning/...` methods are all aliased to `msg` to match the
    PrintLogger / WriteLogger API.
    """

    def __init__(self, *targets: IO[str]) -> None:
        self._targets = targets

    def msg(self, message: str) -> None:
        for t in self._targets:
            try:
                t.write(message + "\n")
                t.flush()
            except Exception:
                # never let a broken sink break the app
                pass

    # structlog method aliases (must match PrintLogger / WriteLogger)
    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg

    def __repr__(self) -> str:
        return f"<FanoutLogger targets={len(self._targets)}>"


class _ConsoleFactory:
    def __call__(self, *args: object, **kwargs: object) -> _FanoutLogger:
        return _FanoutLogger(sys.stderr)


def _file_factory(path: Path) -> _FileFactory:
    return _FileFactory(path)


class _FileFactory:
    def __init__(self, path: Path) -> None:
        self._path = path

    def __call__(self, *args: object, **kwargs: object) -> _FanoutLogger:
        fh = open(self._path, "a", encoding="utf-8")  # noqa: SIM115
        return _FanoutLogger(sys.stderr, fh)


def setup_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO",
    log_file: Path | None = None,
    json_format: bool = True,  # noqa: ARG001 — reserved for Phase 4+
) -> None:
    """Configure structlog.

    Single structlog pipeline; one log call fans out to stderr + (optional) file.
    """
    log_level = _LOG_LEVELS[level]

    # silence stdlib
    root = logging.getLogger()
    root.setLevel(log_level)
    for h in list(root.handlers):
        root.removeHandler(h)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(colors=True),
    ]

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        factory: object = _file_factory(log_file)
        cache = False  # re-open per emit (test isolation; Phase 4: queue)
    else:
        factory = _ConsoleFactory()
        cache = True

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=factory,  # type: ignore[arg-type]
        cache_logger_on_first_use=cache,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger."""
    return structlog.get_logger(name)


__all__ = ["get_logger", "setup_logging"]
