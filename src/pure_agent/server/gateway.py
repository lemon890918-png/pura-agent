"""Gateway: FastAPI app exposing REST + WebSocket endpoints.

Phase 7. The gateway is a thin layer over SessionManager + AIAgentLoop.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pure_agent.agent import AIAgentLoop
from pure_agent.config import load_config
from pure_agent.server.auth import check_api_key
from pure_agent.memory import ContextBuilder
from pure_agent.model import (
    AgentRunResult,
    CanonicalMessage,
    Role,
    TextBlock,
    Usage,
)
from pure_agent.persistence import Database
from pure_agent.plan import (
    PlanAgent,
    PlanRunner,
    PlanStorage,
    StepKind,
)
from pure_agent.server.sessions import SessionManager, SessionState
from pure_agent.tools import (
    GlobTool,
    GrepTool,
    ReadFileTool,
    Sandbox,
    ToolRegistry,
    WebSearchTool,
    WriteFileTool,
)

__version__ = "0.1.0"
_started_at: float = 0.0
_session_manager: SessionManager | None = None
_db: Database | None = None
_project_root: str = ""


# ─── pydantic models for request/response ────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_s: float
    sessions: int


class CreateSessionRequest(BaseModel):
    title: str = "untitled"


class SessionInfo(BaseModel):
    id: str
    title: str
    created_at: float
    last_used_at: float
    n_messages: int


class SessionDetail(BaseModel):
    id: str
    title: str
    messages: list[dict[str, Any]]
    n_messages: int


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    session_id: str
    response: str
    turns: int
    usage: dict[str, int]


class PlanRequest(BaseModel):
    goal: str
    project_root: str | None = None
    max_turns: int = 60


class PlanInfo(BaseModel):
    plan_id: str
    goal_id: str
    status: str
    steps: list[dict[str, Any]]


# ─── tool registry / loop factory ────────────────────────────────────────


def _build_default_registry(project_root: str) -> ToolRegistry:
    from pathlib import Path

    sandbox = Sandbox(root=Path(project_root) if project_root else Path(os.getcwd()))
    reg = ToolRegistry()
    reg.register(ReadFileTool(sandbox))
    reg.register(WriteFileTool(sandbox))
    reg.register(GlobTool(sandbox))
    reg.register(GrepTool(sandbox))
    reg.register(WebSearchTool())
    return reg


def _build_model_provider(model: str, api_key: str, base_url: str | None = None):
    """Build an LLM provider based on the model name."""
    from pure_agent.model.minimax_adapter import MinimaxAdapter
    from pure_agent.model.openai_adapter import OpenAIAdapter

    if "minimax" in model.lower() or "MiniMax" in model:
        if base_url:
            return MinimaxAdapter(api_key=api_key, model=model, base_url=base_url)
        return MinimaxAdapter(api_key=api_key, model=model)
    if base_url:
        return OpenAIAdapter(api_key=api_key, base_url=base_url, default_model=model)
    return OpenAIAdapter(api_key=api_key, default_model=model)


def _build_loop_factory(session: SessionState):
    """Return a factory that builds AIAgentLoop configured for this session."""

    cfg = load_config()
    api_key = (
        cfg.raw.get("minimax_api_key", "")
        or os.environ.get("MINIMAX_API_KEY", "")
    )
    model = cfg.raw.get("default_model", "MiniMax-Text-01") or "MiniMax-Text-01"
    base_url = cfg.raw.get("base_url", "https://api.minimaxi.com/v1")

    provider = _build_model_provider(model, api_key, base_url)
    registry = _build_default_registry(_project_root or os.getcwd())

    base_system_prompt = (
        "You are pure-agent, a typed agent that solves long-running tasks.\n"
        "You may use tools to read/write files, search the web, etc.\n"
        "Be concise and accurate."
    )

    def factory(*, system_prompt: str = "", tools=None, max_turns: int = 10):
        ctx_text = session.context.build() if hasattr(session, "context") else ""
        full_sys = (
            f"{base_system_prompt}\n\n"
            f"{system_prompt}\n\n"
            f"{ctx_text}"
        )
        return AIAgentLoop(
            provider=provider,
            tools=tools or registry,
            model=model,
            system_prompt=full_sys,
            max_turns=max_turns,
        )

    return factory


# ─── app lifecycle ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _started_at, _session_manager, _db, _project_root
    _started_at = time.time()
    home = os.environ.get("PURE_AGENT_HOME", os.path.expanduser("~/.pure-agent"))
    os.makedirs(home, exist_ok=True)
    db_path = os.path.join(home, "memory.db")
    _db = Database(path=db_path)
    _project_root = os.environ.get("PURE_AGENT_PROJECT_ROOT", os.getcwd())
    _session_manager = SessionManager(_db)
    # Phase 9: load persisted sessions from previous runs
    try:
        _session_manager.load_persisted()
    except Exception:
        pass
    # auto-create a default session if none exist
    if _session_manager.count() == 0:
        _session_manager.create(title="default")
    # Phase 9: persist on shutdown
    import atexit
    def _persist_on_exit():
        try:
            if _session_manager is not None:
                _session_manager.persist()
        except Exception:
            pass
    atexit.register(_persist_on_exit)
    yield
    # Final persist before close
    try:
        if _session_manager is not None:
            _session_manager.persist()
    except Exception:
        pass
    if _db:
        _db.conn.close()


app = FastAPI(title="pure-agent gateway", version=__version__, lifespan=lifespan)


def _mgr() -> SessionManager:
    assert _session_manager is not None, "gateway not started"
    return _session_manager


# ─── REST routes ─────────────────────────────────────────────────────────


@app.get("/tools", dependencies=[Depends(check_api_key)])
async def list_tools() -> dict[str, Any]:
    """List all available tools and their schemas (Phase 9 tool manifest)."""
    from pure_agent.server.gateway import _build_default_registry
    reg = _build_default_registry(_project_root or os.getcwd())
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in reg.all()
        ]
    }


@app.get("/health", dependencies=[Depends(check_api_key)])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        uptime_s=time.time() - _started_at,
        sessions=_mgr().count(),
    )


@app.get("/sessions", dependencies=[Depends(check_api_key)])
async def list_sessions() -> dict[str, Any]:
    return {"sessions": [s.to_summary() for s in _mgr().list()]}


@app.post("/sessions", dependencies=[Depends(check_api_key)])
async def create_session(req: CreateSessionRequest) -> SessionInfo:
    s = _mgr().create(title=req.title)
    return SessionInfo(**s.to_summary())


@app.get("/sessions/{session_id}", dependencies=[Depends(check_api_key)])
async def get_session(session_id: str) -> SessionDetail:
    s = _mgr().get(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail="session not found")
    msgs = []
    for m in s.messages:
        msgs.append(
            {
                "role": m.role.value,
                "text": m.text(),
            }
        )
    return SessionDetail(
        id=s.id,
        title=s.title,
        messages=msgs,
        n_messages=len(s.messages),
    )


@app.delete("/sessions/{session_id}", dependencies=[Depends(check_api_key)])
async def delete_session(session_id: str) -> dict[str, bool]:
    ok = _mgr().delete(session_id)
    return {"deleted": ok}


@app.post("/sessions/{session_id}/chat", dependencies=[Depends(check_api_key)])
async def chat(session_id: str, req: ChatRequest) -> ChatResponse:
    s = _mgr().get_or_create(session_id)
    factory = _build_loop_factory(s)
    loop = factory()
    result = await loop.run(req.message)
    s.messages = list(result.messages)
    s.touch()
    return ChatResponse(
        session_id=s.id,
        response=result.final_text or "",
        turns=result.turns,
        usage={
            "prompt_tokens": result.total_usage.prompt_tokens,
            "completion_tokens": result.total_usage.completion_tokens,
            "total_tokens": result.total_usage.total_tokens,
        },
    )


# ─── New UI API endpoints ────────────────────────────────────────────────


class FileNode(BaseModel):
    name: str
    path: str
    type: str  # "file" or "dir"
    children: list["FileNode"] | None = None


@app.get("/files", dependencies=[Depends(check_api_key)])
async def list_files(path: str = "", recursive: bool = False) -> dict[str, Any]:
    """List files in the project directory."""
    from pathlib import Path
    root = Path(_project_root or os.getcwd())
    target = root / path if path else root
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="not a directory")
    
    files = []
    for entry in target.iterdir():
        if entry.name.startswith("."):
            continue
        files.append({
            "name": entry.name,
            "path": str(entry.relative_to(root)),
            "type": "dir" if entry.is_dir() else "file",
        })
    return {"files": sorted(files, key=lambda x: (x["type"] != "dir", x["name"]))}


@app.get("/config", dependencies=[Depends(check_api_key)])
async def get_config() -> dict[str, Any]:
    """Get current configuration."""
    cfg = load_config()
    return {
        "config": {
            "default_model": cfg.raw.get("default_model", "MiniMax-Text-01"),
            "minimax_api_key": cfg.raw.get("minimax_api_key", ""),
            "base_url": cfg.raw.get("base_url", "https://api.minimaxi.com/v1"),
            "project_root": _project_root or os.getcwd(),
        }
    }


class UpdateConfigRequest(BaseModel):
    default_model: str | None = None
    minimax_api_key: str | None = None
    base_url: str | None = None
    project_root: str | None = None


@app.post("/config", dependencies=[Depends(check_api_key)])
async def update_config(req: UpdateConfigRequest) -> dict[str, Any]:
    """Update configuration."""
    cfg = load_config()
    updated = {}
    if req.default_model:
        cfg.raw["default_model"] = req.default_model
        updated["default_model"] = req.default_model
    if req.minimax_api_key:
        cfg.raw["minimax_api_key"] = req.minimax_api_key
        updated["minimax_api_key"] = req.minimax_api_key
    if req.base_url:
        cfg.raw["base_url"] = req.base_url
        updated["base_url"] = req.base_url
    if req.project_root:
        global _project_root
        _project_root = req.project_root
        updated["project_root"] = req.project_root
    # Save config
    cfg.save()
    return {"updated": updated, "status": "ok"}


class TerminalRequest(BaseModel):
    command: str
    cwd: str | None = None


@app.post("/terminal/run", dependencies=[Depends(check_api_key)])
async def run_terminal(req: TerminalRequest) -> dict[str, Any]:
    """Run a terminal command."""
    import subprocess
    import shlex
    cwd = req.cwd or _project_root or os.getcwd()
    try:
        result = subprocess.run(
            shlex.split(req.command),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="command timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/skills", dependencies=[Depends(check_api_key)])
async def list_skills() -> dict[str, Any]:
    """List discovered skills (from ~/.pure-agent/skills/ and project skills/)."""
    from pure_agent.skills import discover_skills

    skills_dirs = [
        Path.home() / ".pure-agent" / "skills",
        Path(_project_root or os.getcwd()) / "skills",
        Path(_project_root or os.getcwd()) / "src" / "pure_agent" / "skills",
    ]
    seen: set[str] = set()
    all_skills = []
    for d in skills_dirs:
        if d.exists():
            for s in discover_skills(d):
                if s.name in seen:
                    continue
                seen.add(s.name)
                all_skills.append({
                    "name": s.name,
                    "description": s.description,
                    "path": str(d),
                })
    return {"skills": all_skills}


class AutomationRequest(BaseModel):
    schedule: str
    prompt: str
    enabled: bool = True


@app.get("/automations", dependencies=[Depends(check_api_key)])
async def list_automations() -> dict[str, Any]:
    """List cron jobs that drive the agent (automation UI)."""
    import json
    cron_file = Path.home() / ".hermes" / "cron_jobs.json"
    if not cron_file.exists():
        return {"automations": []}
    try:
        data = json.loads(cron_file.read_text())
        autos = [
            {
                "id": job.get("id", ""),
                "name": job.get("name") or job.get("prompt", "")[:30],
                "schedule": job.get("schedule", ""),
                "prompt": job.get("prompt", ""),
                "enabled": job.get("enabled", True),
            }
            for job in data.get("jobs", [])
        ]
        return {"automations": autos}
    except Exception as e:
        return {"automations": [], "error": str(e)}


@app.get("/diff", dependencies=[Depends(check_api_key)])
async def get_diff(path: str | None = None) -> dict[str, Any]:
    """Get git diff for the project or specific file."""
    import subprocess
    import shlex
    cwd = _project_root or os.getcwd()
    try:
        cmd = ["git", "diff"]
        if path:
            cmd.append(path)
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True
        )
        return {"diff": result.stdout, "exit_code": result.returncode}
    except Exception as e:
        return {"diff": "", "error": str(e)}


# ─── WebSocket ───────────────────────────────────────────────────────────
async def create_plan(session_id: str, req: PlanRequest) -> PlanInfo:
    s = _mgr().get_or_create(session_id)
    project_root = req.project_root or _project_root
    storage = PlanStorage(_db)
    factory = _build_loop_factory(s)

    # Use PlanAgent to decompose
    from pure_agent.model.minimax_adapter import MinimaxAdapter
    from pure_agent.model.openai_adapter import OpenAIAdapter
    from pure_agent.model.provider import ProviderAdapter

    cfg = load_config()
    api_key = (
        cfg.raw.get("minimax_api_key", "")
        or os.environ.get("MINIMAX_API_KEY", "")
    )
    model = cfg.raw.get("default_model", "MiniMax-Text-01")
    base_url = cfg.raw.get("base_url", "https://api.minimaxi.com/v1")
    provider = _build_model_provider(model, api_key, base_url)
    plan_agent = PlanAgent(provider, model)

    # create goal
    goal = storage.create_goal(project_id="default", text=req.goal)
    storage.update_goal_status(goal.id, "running")
    # decompose
    try:
        plan, usage = await plan_agent.decompose(goal)
        storage.create_plan(plan)
        # run
        runner = PlanRunner(storage=storage, loop_factory=factory)
        result = await runner.execute(plan.id, max_total_turns=req.max_turns)
        storage.update_goal_status(goal.id, "done" if result.ok else "failed")
    except Exception as e:
        storage.update_goal_status(goal.id, "failed")
        raise HTTPException(status_code=500, detail=str(e))

    # get plan + steps
    plan_obj = storage.get_plan(plan.id)
    steps_info = []
    for step in plan_obj.steps:
        steps_info.append(
            {
                "id": step.id,
                "idx": step.idx,
                "kind": step.kind.value,
                "action": step.action,
                "status": step.status.value,
                "attempts": step.attempts,
            }
        )
    return PlanInfo(
        plan_id=plan.id,
        goal_id=goal.id,
        status=plan_obj.status.value,
        steps=steps_info,
    )


# ─── WebSocket ───────────────────────────────────────────────────────────


@app.websocket("/ws/sessions/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    s = _mgr().get_or_create(session_id)
    try:
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "ping":
                await websocket.send_json({"type": "pong"})
            elif mtype == "chat":
                # Real chat needs a working model provider. Build only if key is set.
                api_key = os.environ.get("MINIMAX_API_KEY", "")
                if not api_key:
                    await websocket.send_json(
                        {"type": "error", "message": "no MINIMAX_API_KEY set"}
                    )
                    continue
                factory = _build_loop_factory(s)
                loop = factory()
                user_msg = msg.get("message", "")
                try:
                    result = await loop.run(user_msg)
                    await websocket.send_json(
                        {
                            "type": "result",
                            "turns": result.turns,
                            "text": result.final_text or "",
                            "usage": {
                                "prompt": result.total_usage.prompt_tokens,
                                "completion": result.total_usage.completion_tokens,
                                "total": result.total_usage.total_tokens,
                            },
                        }
                    )
                    s.touch()
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": str(e)})
            elif mtype == "steer":
                # No-op if no active loop; otherwise queue
                await websocket.send_json({"type": "steer_received", "message": msg.get("message", "")})
            elif mtype == "abort":
                await websocket.send_json({"type": "aborted"})
            else:
                await websocket.send_json({"type": "error", "message": f"unknown type: {mtype}"})
    except WebSocketDisconnect:
        pass


async def _stream_loop(loop: AIAgentLoop, user_message: str):
    """Yield stream events as JSON-friendly dicts."""
    # We collect events by intercepting loop._emit, but a simpler approach:
    # we patch the stream by wrapping provider.stream.
    from pure_agent.model import ModelEvent
    orig = loop.provider.stream

    async def wrapped(request):
        async for ev in orig(request):
            yield ev
            # convert to ws event
            if ev.type == "text_delta":
                yield {"type": "ws", "payload": {"type": "text_delta", "text": ev.text}}
            elif ev.type == "tool_call_delta":
                yield {"type": "ws", "payload": {"type": "tool_call", "name": ev.tool_name, "args": ev.tool_arguments_delta}}
            elif ev.type == "usage":
                yield {"type": "ws", "payload": {"type": "usage", "prompt": ev.usage.prompt_tokens, "completion": ev.usage.completion_tokens}}
            elif ev.type == "message_end":
                yield {"type": "ws", "payload": {"type": "message_end", "reason": ev.finish_reason}}

    # Run loop, but bypass for simplicity: just call run() and emit final
    result = await loop.run(user_message)
    yield {"type": "result", "turns": result.turns, "usage": {"prompt": result.total_usage.prompt_tokens, "completion": result.total_usage.completion_tokens, "total": result.total_usage.total_tokens}, "text": result.final_text or ""}


# ─── main (for `python -m pure_agent.server.gateway`) ──────────────────


def main() -> None:
    import uvicorn

    host = os.environ.get("PURE_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("PURE_AGENT_PORT", "18790"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
