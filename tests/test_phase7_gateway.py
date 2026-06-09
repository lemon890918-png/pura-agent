"""Tests for Phase 7 Gateway: SessionManager + REST endpoints + WebSocket."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from pure_agent.persistence import Database
from pure_agent.server import SessionManager, SessionState, app
from pure_agent.server.gateway import _build_default_registry
from pure_agent.tools import Sandbox, ToolRegistry


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(path=tmp_path / "test.db")
    return d


@pytest.fixture
def mgr(db) -> SessionManager:
    return SessionManager(db)


# ─── SessionManager ─────────────────────────────────────────────────────


@pytest.mark.smoke
def test_session_manager_create(mgr) -> None:
    s = mgr.create(title="test")
    assert s.id.startswith("ses_")
    assert s.title == "test"
    assert s.created_at > 0
    assert s.last_used_at > 0
    assert s.messages == []
    assert s.context is not None
    assert s.memory is not None


@pytest.mark.smoke
def test_session_manager_get(mgr) -> None:
    s = mgr.create()
    assert mgr.get(s.id) is s
    assert mgr.get("nonexistent") is None


@pytest.mark.smoke
def test_session_manager_get_or_create(mgr) -> None:
    s1 = mgr.get_or_create("s1")
    assert s1.id == "s1"
    s2 = mgr.get_or_create("s1")
    assert s2 is s1  # same instance


@pytest.mark.smoke
def test_session_manager_list(mgr) -> None:
    mgr.create("a")
    mgr.create("b")
    sessions = mgr.list()
    assert len(sessions) == 2


@pytest.mark.smoke
def test_session_manager_delete(mgr) -> None:
    s = mgr.create()
    assert mgr.delete(s.id) is True
    assert mgr.get(s.id) is None
    assert mgr.delete("nonexistent") is False


@pytest.mark.smoke
def test_session_state_touch(mgr) -> None:
    s = mgr.create()
    initial = s.last_used_at
    time.sleep(0.05)
    s.touch()
    assert s.last_used_at > initial


@pytest.mark.smoke
def test_session_state_to_summary(mgr) -> None:
    s = mgr.create("hello")
    summary = s.to_summary()
    assert summary["id"] == s.id
    assert summary["title"] == "hello"
    assert summary["n_messages"] == 0


@pytest.mark.smoke
def test_session_state_lock(mgr) -> None:
    s = mgr.create()
    assert isinstance(s.lock, asyncio.Lock)
    # lock can be acquired
    async def go():
        async with s.lock:
            return True
    assert run(go())


# ─── default registry ───────────────────────────────────────────────────


@pytest.mark.smoke
def test_build_default_registry(tmp_path) -> None:
    reg = _build_default_registry(str(tmp_path))
    names = [t.name for t in reg.all()]
    assert "read_file" in names
    assert "write_file" in names
    assert "glob" in names
    assert "grep" in names
    assert "web_search" in names


# ─── FastAPI app ────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_app_creation() -> None:
    assert app is not None
    paths = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/health" in paths
    assert "/sessions" in paths
    assert "/sessions/{session_id}/chat" in paths
    assert "/sessions/{session_id}/plan" in paths


# ─── /health endpoint via TestClient ───────────────────────────────────


@pytest.mark.smoke
def test_health_endpoint(tmp_path) -> None:
    import os
    os.environ["PURE_AGENT_HOME"] = str(tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "uptime_s" in body
        assert "sessions" in body


@pytest.mark.smoke
def test_sessions_crud_endpoint(tmp_path) -> None:
    import os
    os.environ["PURE_AGENT_HOME"] = str(tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        # list
        r = client.get("/sessions")
        assert r.status_code == 200
        assert "sessions" in r.json()
        # create
        r = client.post("/sessions", json={"title": "test"})
        assert r.status_code == 200
        sid = r.json()["id"]
        # get
        r = client.get(f"/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["title"] == "test"
        # delete
        r = client.delete(f"/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        # 404 after delete
        r = client.get(f"/sessions/{sid}")
        assert r.status_code == 404


@pytest.mark.smoke
def test_chat_endpoint_real_llm(tmp_path) -> None:
    """Chat with real LLM through the gateway."""
    import os
    os.environ["PURE_AGENT_HOME"] = str(tmp_path)
    # read key
    from dotenv import dotenv_values
    hermes_env = dotenv_values("/Users/wenxin/.hermes/.env")
    key = hermes_env.get("MINIMAX_API_KEY", "")
    if not key:
        pytest.skip("no MINIMAX_API_KEY in /Users/wenxin/.hermes/.env")
    os.environ["MINIMAX_API_KEY"] = key

    # write key to a config.yaml in PURE_AGENT_HOME
    cfg_path = Path(tmp_path) / "config.yaml"
    cfg_path.write_text(f"minimax_api_key: '{key}'\ndefault_model: MiniMax-Text-01\nbase_url: https://api.minimaxi.com/v1\n")

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        r = client.post("/sessions", json={"title": "chat-test"})
        sid = r.json()["id"]
        r = client.post(
            f"/sessions/{sid}/chat",
            json={"message": "Reply with exactly: ACK"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "ACK" in body["response"]


@pytest.mark.smoke
def test_websocket_ping(tmp_path) -> None:
    """WebSocket ping/pong works."""
    import os
    os.environ["PURE_AGENT_HOME"] = str(tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        with client.websocket_connect("/ws/sessions/test") as ws:
            ws.send_json({"type": "ping"})
            resp = ws.receive_json()
            assert resp["type"] == "pong"


@pytest.mark.smoke
def test_websocket_steer(tmp_path) -> None:
    """Steer message via WS is acknowledged."""
    import os
    os.environ["PURE_AGENT_HOME"] = str(tmp_path)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        with client.websocket_connect("/ws/sessions/test") as ws:
            ws.send_json({"type": "steer", "message": "hint here"})
            resp = ws.receive_json()
            assert resp["type"] == "steer_received"
