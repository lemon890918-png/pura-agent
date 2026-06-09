"""Tests for Phase 9 polish: persistence, auth, tools endpoint, port config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pure_agent.persistence import Database
from pure_agent.server import SessionManager
from pure_agent.server.auth import check_api_key


# ─── SessionManager persistence ──────────────────────────────────────


@pytest.mark.smoke
def test_session_manager_persist(tmp_path) -> None:
    db = Database(path=tmp_path / "test.db")
    mgr = SessionManager(db)
    s1 = mgr.create("alpha")
    s2 = mgr.create("beta")
    n = mgr.persist()
    assert n == 2
    # check rows in db
    rows = db.conn.execute(
        "SELECT * FROM sessions WHERE project_id = 'default'"
    ).fetchall()
    assert len(rows) == 2


@pytest.mark.smoke
def test_session_manager_load_persisted(tmp_path) -> None:
    db = Database(path=tmp_path / "test.db")
    # 1st run: create + persist
    mgr1 = SessionManager(db)
    s1 = mgr1.create("alpha")
    s1_id = s1.id
    mgr1.persist()

    # 2nd run: load
    mgr2 = SessionManager(db)
    n = mgr2.load_persisted()
    assert n == 1
    s1_reloaded = mgr2.get(s1_id)
    assert s1_reloaded is not None
    assert s1_reloaded.title == "alpha"


@pytest.mark.smoke
def test_session_manager_load_no_duplicate(tmp_path) -> None:
    db = Database(path=tmp_path / "test.db")
    mgr = SessionManager(db)
    s1 = mgr.create("alpha")
    mgr.persist()
    n = mgr.load_persisted()
    # in-memory already has it → no duplicate
    assert n == 0


# ─── auth ─────────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_auth_no_key_allows() -> None:
    """If PURE_AGENT_API_KEY is unset, all requests are allowed."""
    if "PURE_AGENT_API_KEY" in os.environ:
        del os.environ["PURE_AGENT_API_KEY"]
    # should not raise
    class FakeRequest:
        client = type("Client", (), {"host": "127.0.0.1"})()
    check_api_key(FakeRequest())


@pytest.mark.smoke
def test_auth_localhost_bypass() -> None:
    """Localhost bypasses auth even when key is set."""
    os.environ["PURE_AGENT_API_KEY"] = "secret-123"

    class FakeRequest:
        client = type("Client", (), {"host": "127.0.0.1"})()

    check_api_key(FakeRequest())  # no raise


@pytest.mark.smoke
def test_auth_remote_rejects_no_key() -> None:
    """Remote request without key gets 401."""
    from fastapi import HTTPException

    os.environ["PURE_AGENT_API_KEY"] = "secret-123"

    class FakeRequest:
        client = type("Client", (), {"host": "10.0.0.5"})()
        headers: dict = {}
        query_params: dict = {}

    with pytest.raises(HTTPException) as exc:
        check_api_key(FakeRequest())
    assert exc.value.status_code == 401


@pytest.mark.smoke
def test_auth_remote_accepts_correct_key() -> None:
    """Remote request with correct key passes."""
    os.environ["PURE_AGENT_API_KEY"] = "secret-123"

    class FakeRequest:
        client = type("Client", (), {"host": "10.0.0.5"})()
        headers = {"X-API-Key": "secret-123"}
        query_params: dict = {}

    check_api_key(FakeRequest())  # no raise


@pytest.mark.smoke
def test_auth_remote_rejects_wrong_key() -> None:
    """Remote request with wrong key gets 401."""
    from fastapi import HTTPException

    os.environ["PURE_AGENT_API_KEY"] = "secret-123"

    class FakeRequest:
        client = type("Client", (), {"host": "10.0.0.5"})()
        headers = {"X-API-Key": "wrong"}
        query_params: dict = {}

    with pytest.raises(HTTPException) as exc:
        check_api_key(FakeRequest())
    assert exc.value.status_code == 401


# ─── /tools endpoint ────────────────────────────────────────────────


@pytest.mark.smoke
def test_tools_endpoint(tmp_path) -> None:
    """GET /tools returns the tool manifest."""
    # ensure no API key
    if "PURE_AGENT_API_KEY" in os.environ:
        del os.environ["PURE_AGENT_API_KEY"]
    os.environ["PURE_AGENT_HOME"] = str(tmp_path)
    from fastapi.testclient import TestClient
    from pure_agent.server import app

    with TestClient(app) as client:
        r = client.get("/tools")
        assert r.status_code == 200, f"got {r.status_code}: {r.text}"
        body = r.json()
        assert "tools" in body
        names = {t["name"] for t in body["tools"]}
        assert "read_file" in names
        assert "write_file" in names
        assert "glob" in names
        # each tool has parameters (JSON Schema)
        for tool in body["tools"]:
            assert "name" in tool
            assert "parameters" in tool


# ─── Port config ────────────────────────────────────────────────────


@pytest.mark.smoke
def test_default_port_is_18790() -> None:
    """Phase 9: default port is 18790 (avoid PilotDeck 18789)."""
    from pure_agent.server import gateway
    import pure_agent.cli.serve_cli as cli

    # check default in gateway main
    import inspect
    src = inspect.getsource(gateway)
    assert "18790" in src
    # check default in CLI
    src = inspect.getsource(cli)
    assert "18790" in src
