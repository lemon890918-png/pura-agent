"""Test fixtures shared across the suite."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Create a temporary PURE_AGENT_HOME for the test."""
    tmp = Path(tempfile.mkdtemp(prefix="pure-agent-test-"))
    monkeypatch.setenv("PURE_AGENT_HOME", str(tmp))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def isolated_db(tmp_home: Path) -> Path:
    """Return the path to a fresh memory.db under tmp_home."""
    return tmp_home / "memory.db"
