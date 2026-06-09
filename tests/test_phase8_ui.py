"""Tests for Phase 8 UI HTML/JS structure."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


@pytest.fixture
def ui_path() -> Path:
    return Path(__file__).parent.parent / "ui" / "index.html"


@pytest.mark.smoke
def test_ui_file_exists(ui_path) -> None:
    assert ui_path.exists()
    assert ui_path.stat().st_size > 1000


@pytest.mark.smoke
def test_ui_has_required_elements(ui_path) -> None:
    content = ui_path.read_text()
    # must have all the standard UI sections
    assert "id=\"sessions\"" in content
    assert "id=\"messages\"" in content
    assert "id=\"input\"" in content
    assert "id=\"status\"" in content
    assert "id=\"send-btn\"" in content


@pytest.mark.smoke
def test_ui_has_required_js_functions(ui_path) -> None:
    content = ui_path.read_text()
    # must declare all required functions
    for fn in ["health", "listSessions", "newSession", "openSession", "send"]:
        assert f"function {fn}" in content, f"missing function {fn}"


@pytest.mark.smoke
def test_ui_calls_correct_endpoints(ui_path) -> None:
    content = ui_path.read_text()
    # must call the gateway endpoints
    assert "/health" in content
    assert "/sessions" in content
    assert "/chat" in content


@pytest.mark.smoke
def test_ui_has_gateway_url(ui_path) -> None:
    content = ui_path.read_text()
    # Phase 9: read from ?gateway= query string with default 18790
    assert "urlParams" in content or "URLSearchParams" in content
    assert "18790" in content
    # must reference gateway URL pattern
    assert "127.0.0.1" in content
    print("UI reads gateway port from query string with default 18790")


@pytest.mark.smoke
def test_ui_has_styles(ui_path) -> None:
    content = ui_path.read_text()
    # must include a <style> block
    assert "<style>" in content
    assert "</style>" in content
    # dark theme (the brand is consistent with PilotDeck)
    assert "#0e0e10" in content or "background:" in content
