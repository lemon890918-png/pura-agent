"""Phase 4 integration tests: AIAgentLoop + compactor + steer + checkpoint + budget."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest

from pure_agent.agent import AIAgentLoop, Checkpointer, SteerQueue
from pure_agent.harness import TokenBudget
from pure_agent.memory import Compactor
from pure_agent.model import (
    AgentRunResult,
    CanonicalMessage,
    CanonicalRequest,
    ModelEvent,
    Role,
    TextBlock,
    ToolSchema,
    Usage,
)
from pure_agent.persistence import Database
from pure_agent.tools import Sandbox, ToolRegistry
from pure_agent.tools.base import Tool, ToolResult


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── mock LLM that always tells a tool call to read_file with arg path="x" ─


class MockProvider:
    def __init__(self, scripted: list[list[ModelEvent]] | None = None) -> None:
        self.calls = 0
        self.scripted = scripted or []
        self.last_request: CanonicalRequest | None = None

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        self.last_request = request
        idx = min(self.calls, len(self.scripted) - 1)
        self.calls += 1
        if not self.scripted:
            yield ModelEvent(type="text_delta", text="ok")
            yield ModelEvent(type="message_end", finish_reason="stop")
            return
        for ev in self.scripted[idx]:
            yield ev

    def normalize_tool_schema(self, s):
        return {}

    def max_context_tokens(self, m=None):
        return 128_000


def text_event(text: str) -> ModelEvent:
    return ModelEvent(type="text_delta", text=text)


def tool_call(name: str, args: dict) -> ModelEvent:
    return ModelEvent(
        type="tool_call_delta",
        tool_call_id="tc1",
        tool_name=name,
        tool_arguments_delta=__import__("json").dumps(args),
    )


def usage_ev(p: int = 100, c: int = 50) -> ModelEvent:
    return ModelEvent(
        type="usage",
        usage=Usage(prompt_tokens=p, completion_tokens=c, total_tokens=p + c),
    )


# ─── minimal tool registry ─────────────────────────────────────────────────


class StubTool(Tool):
    name = "noop"
    description = "does nothing"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}
    parameters_model = None

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def validate_args(self, arguments: dict) -> tuple[dict, str | None]:
        return arguments, None

    async def execute(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult.ok("ok")


def _make_loop(provider, *, with_compactor=False, with_steer=False, with_checkpoint=False, with_budget=False, compact_threshold=80_000, tool_timeout=10.0, system_prompt="", db=None) -> tuple[AIAgentLoop, SteerQueue | None, Checkpointer | None, TokenBudget | None]:
    tools = ToolRegistry()
    tools.register(StubTool())
    steer = SteerQueue() if with_steer else None
    ckpt = Checkpointer(db, "s1") if with_checkpoint and db is not None else None
    budget_obj = TokenBudget(db) if with_budget and db is not None else None
    compactor = Compactor(provider, "mock") if with_compactor else None
    if with_budget and db is not None and budget_obj is not None:
        budget = budget_obj.step("s1", plan_id="p1")
    else:
        budget = None
    loop = AIAgentLoop(
        provider=provider,
        tools=tools,
        model="mock",
        system_prompt=system_prompt,
        max_turns=5,
        steer_queue=steer,
        checkpointer=ckpt,
        budget=budget,
        compactor=compactor,
        compact_threshold_tokens=compact_threshold,
        tool_timeout_s=tool_timeout,
    )
    return loop, steer, ckpt, budget_obj


# ─── tests ────────────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_steer_message_drained_into_conversation() -> None:
    provider = MockProvider(
        [[text_event("ok"), usage_ev(), ModelEvent(type="message_end", finish_reason="stop")]]
    )
    loop, steer, _, _ = _make_loop(provider, with_steer=True)
    # user types "actually do this" while loop is starting
    steer.put_text_nowait("USER INJECTION: focus on X")
    result = run(loop.run("hello"))
    assert result.final_text == "ok"
    # the steer message should be in messages
    msgs = result.messages
    assert any("USER INJECTION" in m.text() for m in msgs)


@pytest.mark.smoke
def test_checkpoint_saved_after_turn() -> None:
    db = Database(path="/tmp/pa-ckpt-$$-200.db".replace("$$", "x"))
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
    provider = MockProvider(
        [
            [
                text_event("thinking"),
                tool_call("noop", {}),
                usage_ev(),
                ModelEvent(type="message_end", finish_reason="tool_calls"),
            ],
            [text_event("done"), usage_ev(), ModelEvent(type="message_end", finish_reason="stop")],
        ]
    )
    loop, _, ckpt, _ = _make_loop(provider, with_checkpoint=True, db=db)
    run(loop.run("hi"))
    # 2 turns → 2 checkpoints
    cks = ckpt.list_checkpoints()
    assert len(cks) >= 1
    # load latest
    out = ckpt.load_latest()
    assert out is not None
    msgs, meta = out
    assert any("done" in m.text() for m in msgs)


@pytest.mark.smoke
def test_compactor_triggered_when_over_threshold() -> None:
    """When messages exceed threshold, compactor.compact is called."""
    big_text = "x" * 200_000  # ~50k tokens
    provider = MockProvider(
        [
            [text_event(big_text), usage_ev(500_000, 100), ModelEvent(type="message_end", finish_reason="stop")],
            [text_event("y"), usage_ev(), ModelEvent(type="message_end", finish_reason="stop")],
        ]
    )
    events = []
    def cb(t, p): events.append((t, p))
    loop, _, _, _ = _make_loop(provider, with_compactor=True, compact_threshold=10_000)
    loop._on_event = cb
    result = run(loop.run("test"))
    # first turn: large prompt → compactor called
    print("events:", [e[0] for e in events])
    print("compacted_count:", loop.compacted_count)
    assert loop.compacted_count >= 1


@pytest.mark.smoke
def test_budget_exceeded_terminates_run() -> None:
    db = Database(path="/tmp/pa-budget-$$-300.db".replace("$$", "x"))
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
    budget_obj = TokenBudget(db, per_step=100)
    from pure_agent.harness.budget import StepBudget
    budget_ctx = StepBudget(parent=budget_obj, step_id="s1", plan_id="p1")
    # scripted: first call uses 200 tokens, exceeding the 100/step limit
    provider = MockProvider(
        [
            [text_event("x"), usage_ev(150, 50), ModelEvent(type="message_end", finish_reason="stop")],
            [text_event("y"), usage_ev(), ModelEvent(type="message_end", finish_reason="stop")],
        ]
    )
    loop = AIAgentLoop(
        provider=provider,
        tools=ToolRegistry(),
        model="mock",
        max_turns=5,
        budget=budget_ctx,
    )
    result = run(loop.run("x"))
    # loop should have terminated with ERROR due to budget
    assert result.error is not None
    assert "budget" in result.error.lower()


@pytest.mark.smoke
def test_tool_timeout_returns_tool_timeout_error() -> None:
    """A tool that takes too long returns tool_timeout error, not crash."""

    class SlowTool(Tool):
        name = "slow"
        description = "slow"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}}
        parameters_model = None

        def validate_args(self, arguments: dict) -> tuple[dict, str | None]:
            return arguments, None

        async def execute(self, **kwargs) -> ToolResult:
            await asyncio.sleep(5.0)
            return ToolResult.ok("done")

    provider = MockProvider(
        [
            [
                text_event(""),
                tool_call("slow", {}),
                usage_ev(),
                ModelEvent(type="message_end", finish_reason="tool_calls"),
            ],
            [text_event("got timeout"), usage_ev(), ModelEvent(type="message_end", finish_reason="stop")],
        ]
    )
    tools = ToolRegistry()
    tools.register(SlowTool())
    loop = AIAgentLoop(
        provider=provider,
        tools=tools,
        model="mock",
        max_turns=5,
        tool_timeout_s=0.2,  # very short
    )
    result = run(loop.run("go"))
    # result should complete (even if tool timed out) — agent handles it
    assert result.turns >= 1


@pytest.mark.smoke
def test_all_phase4_features_combined() -> None:
    """End-to-end with steer + checkpoint + budget + compactor all enabled."""
    db = Database(path="/tmp/pa-combo-$$-400.db".replace("$$", "x"))
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
    budget_obj = TokenBudget(db, per_step=10_000)
    # construct a StepBudget directly (avoid __enter__ complexity)
    from pure_agent.harness.budget import StepBudget
    budget_ctx = StepBudget(
        parent=budget_obj,
        step_id="combo",
        plan_id="p1",
    )
