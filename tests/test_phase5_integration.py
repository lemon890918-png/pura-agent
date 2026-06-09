"""Phase 5 integration: 4 subagents + PlanRunner integration + harness wrap."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from pure_agent.agent import (
    AIAgentLoop,
    SubagentRequest,
    SubagentResponse,
    SubagentRole,
    SubagentStatus,
    build_request,
    filter_registry,
    run_subagent,
)
from pure_agent.harness import (
    RetryPolicy,
    RetryStrategy,
    TimeoutPolicy,
    Tracer,
    with_retry,
)
from pure_agent.model import (
    CanonicalRequest,
    ModelEvent,
    Role,
    TextBlock,
    ToolUseBlock,
    Usage,
)
from pure_agent.persistence import Database
from pure_agent.plan import (
    Plan,
    PlanRunner,
    PlanStatus,
    PlanStep,
    PlanStorage,
    StepKind,
    StepStatus,
)
from pure_agent.tools import (
    GlobTool,
    GrepTool,
    ReadFileTool,
    Sandbox,
    ToolRegistry,
    WriteFileTool,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── mock LLM with tool-call scripting ─────────────────────────────────


class ScriptedProvider:
    """Returns scripted ModelEvent sequences one per call."""

    def __init__(self, scripts: list[list[ModelEvent]] | None = None) -> None:
        self.scripts = scripts or []
        self.call_count = 0

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        idx = min(self.call_count, len(self.scripts) - 1)
        self.call_count += 1
        for ev in self.scripts[idx]:
            yield ev

    def normalize_tool_schema(self, s):
        return {}

    def max_context_tokens(self, m=None):
        return 128_000


def text(t: str) -> ModelEvent:
    return ModelEvent(type="text_delta", text=t)


def usage(p: int = 10, c: int = 5) -> ModelEvent:
    return ModelEvent(type="usage", usage=Usage(prompt_tokens=p, completion_tokens=c, total_tokens=p + c))


def end(reason: str = "stop") -> ModelEvent:
    return ModelEvent(type="message_end", finish_reason=reason)


def tool_call(name: str, args: dict, id_: str = "tc1") -> ModelEvent:
    return ModelEvent(
        type="tool_call_delta",
        tool_call_id=id_,
        tool_name=name,
        tool_arguments_delta=json.dumps(args),
    )


# ─── test 1: explore subagent is read-only ──────────────────────────────


@pytest.mark.smoke
def test_explore_subagent_cannot_write(tmp_path) -> None:
    """Explore subagent has no write_file tool even if registry has it."""
    sandbox = Sandbox(root=tmp_path)
    reg = ToolRegistry()
    reg.register(ReadFileTool(sandbox))
    reg.register(WriteFileTool(sandbox))
    reg.register(GlobTool(sandbox))
    reg.register(GrepTool(sandbox))

    req = build_request(SubagentRole.EXPLORE, "list files", task_id="e1")
    filtered = filter_registry(reg, req)
    names = [t.name for t in filtered.all()]
    assert "read_file" in names
    assert "write_file" not in names
    assert "edit_file" not in names

    # system prompt is read-only
    sp = make_subagent_system_prompt_(req)
    assert "READ-ONLY" in sp
    assert "NEVER" in sp or "Do NOT" in sp


def make_subagent_system_prompt_(req):
    from pure_agent.agent import make_subagent_system_prompt
    return make_subagent_system_prompt(req)


# ─── test 2: 4 subagents all registered ───────────────────────────────


@pytest.mark.smoke
def test_four_built_in_subagents() -> None:
    from pure_agent.agent import list_roles, get_spec

    roles = list_roles()
    assert len(roles) == 4
    for role in roles:
        spec = get_spec(role)
        assert spec.system_prompt
        assert isinstance(spec.tools, list)
        # all read-only except general_purpose
        if role != SubagentRole.GENERAL_PURPOSE:
            assert spec.read_only is True
            assert "write_file" not in spec.tools


# ─── test 3: harness retry works on transient failures ───────────────


@pytest.mark.smoke
def test_harness_with_retry_recovers() -> None:
    """A flaky operation recovers via retry."""

    call_count = {"n": 0}

    async def flaky() -> str:
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    async def go() -> str:
        return await with_retry(
            flaky,
            policy=RetryPolicy(
                max_attempts=5,
                strategy=RetryStrategy.FIXED,
                initial_backoff_s=0.001,
                retryable_errors=[],  # retry all
            ),
        )

    result = run(go())
    assert result == "ok"
    assert call_count["n"] == 3


# ─── test 4: harness tracer records to db ─────────────────────────────


@pytest.mark.smoke
def test_harness_tracer_persists_to_db(tmp_path) -> None:
    db = Database(path=tmp_path / "test.db")
    t = Tracer(session_id="s1", db=db)

    with t.span("plan_run", plan_id="p1"):
        with t.span("step_run", step_id="s1"):
            pass
        with t.span("step_run", step_id="s2"):
            pass

    rows = db.conn.execute(
        "SELECT * FROM traces WHERE session_id = 's1' ORDER BY created_at"
    ).fetchall()
    assert len(rows) == 3  # 1 plan + 2 steps
    assert sum(1 for r in rows if r["event_type"] == "step_run") == 2


# ─── test 5: PlanRunner token累加（Phase 4 遗留） ─────────────────────


@pytest.mark.smoke
def test_plan_runner_aggregates_tokens(tmp_path) -> None:
    """PlanRunner._run_step accumulates total_usage across steps."""
    db = Database(path=tmp_path / "test.db")
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    db.conn.execute(
        "INSERT OR IGNORE INTO projects (id, name, hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("default", "Default", "default", now, now),
    )
    db.conn.execute(
        "INSERT OR IGNORE INTO sessions (id, project_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("s1", "default", "s1", now, now),
    )
    storage = PlanStorage(db)
    # 2-step plan, each step uses 100/50 tokens via mock
    # runner expects a StepReport JSON block in the final assistant text
    def verdict_json(verdict: str = "pass") -> str:
        return f"```json\n{{\"verdict\": \"{verdict}\", \"summary\": \"ok\"}}\n```"

    provider = ScriptedProvider([
        [text(verdict_json("pass")), usage(100, 50), end()],
        [text(verdict_json("pass")), usage(200, 100), end()],
    ])

    from pure_agent.agent import AIAgentLoop

    def factory(*, system_prompt: str = "", tools=None, max_turns: int = 5):
        return AIAgentLoop(
            provider=provider,
            tools=tools or ToolRegistry(),
            model="mock",
            system_prompt=system_prompt,
            max_turns=max_turns,
        )

    runner = PlanRunner(storage=storage, loop_factory=factory)
    goal = storage.create_goal(project_id="default", text="test goal")
    plan = Plan(goal_id=goal.id)
    s0 = PlanStep(plan_id=plan.id, idx=0, kind=StepKind.READ, action="a")
    s1 = PlanStep(plan_id=plan.id, idx=1, kind=StepKind.READ, action="b", deps=[s0.id])
    plan.steps = [s0, s1]
    storage.create_plan(plan)

    result = run(runner.execute(plan.id, max_total_turns=20))
    # 2 steps × (100+50) = 300 tokens
    assert result.total_usage.total_tokens == 450  # 100+50+200+100
    assert result.total_usage.prompt_tokens == 300
    assert result.total_usage.completion_tokens == 150


# ─── test 6: subagent + harness end-to-end ────────────────────────────


@pytest.mark.smoke
def test_explore_subagent_end_to_end(tmp_path) -> None:
    """Explore subagent runs an LLM, returns DONE, no writes."""
    provider = ScriptedProvider([
        [text("I found 2 files"), usage(10, 5), end()],
    ])
    from pure_agent.agent import AIAgentLoop

    def factory(*, system_prompt: str = "", tools=None, max_turns: int = 5):
        return AIAgentLoop(
            provider=provider,
            tools=tools or ToolRegistry(),
            model="mock",
            system_prompt=system_prompt,
            max_turns=max_turns,
        )

    sandbox = Sandbox(root=tmp_path)
    reg = ToolRegistry()
    reg.register(ReadFileTool(sandbox))
    reg.register(GlobTool(sandbox))

    req = build_request(SubagentRole.EXPLORE, "find files", task_id="e1")
    result = run(run_subagent(req, loop_factory=factory, tool_registry=reg))
    assert result.status == SubagentStatus.DONE
    assert "2 files" in result.summary
    assert result.usage.total_tokens == 15
    assert result.role == SubagentRole.EXPLORE
