"""Tests for logging setup."""

from __future__ import annotations

import re

import pytest
import structlog


@pytest.mark.smoke
def test_setup_logging_console_only(capfd) -> None:
    from pure_agent.logging import setup_logging

    setup_logging(level="INFO")
    structlog.get_logger("test").info("hello_console")
    captured = capfd.readouterr().err
    assert "hello_console" in captured


@pytest.mark.smoke
def test_setup_logging_with_file(tmp_home, capfd) -> None:
    from pure_agent.logging import setup_logging

    log_file = tmp_home / "test.log"
    setup_logging(level="INFO", log_file=log_file, json_format=True)

    structlog.get_logger("test").info("hello_file", foo="bar")
    capfd.readouterr()  # discard stderr

    content = log_file.read_text()
    assert "hello_file" in content
    assert "foo=bar" in content or "bar" in content


@pytest.mark.smoke
def test_log_level_filter(tmp_home, capfd) -> None:
    from pure_agent.logging import setup_logging

    log_file = tmp_home / "test.log"
    setup_logging(level="WARNING", log_file=log_file, json_format=True)

    log = structlog.get_logger("test")
    log.info("should_be_filtered")
    log.warning("should_appear")
    capfd.readouterr()  # discard stderr

    content = log_file.read_text()
    assert "should_be_filtered" not in content
    assert "should_appear" in content


@pytest.mark.smoke
def test_log_emits_timestamp_and_level(tmp_home, capfd) -> None:
    from pure_agent.logging import setup_logging

    log_file = tmp_home / "test.log"
    setup_logging(level="INFO", log_file=log_file, json_format=True)
    structlog.get_logger("test").info("ts_test")
    capfd.readouterr()

    content = log_file.read_text()
    # ISO timestamp
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", content)
    # level
    assert "info" in content.lower() or "INFO" in content
