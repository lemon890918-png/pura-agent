"""Tests for Subagent (Phase 5)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from pure_agent.agent import (
    SubagentRequest,
    SubagentResponse,
    SubagentRole,
    SubagentStatus,
    build_request,
    filter_registry,
    get_spec,
    list_roles,
    make_subagent_system_prompt,
    run_subagent,
)
from pure_agent.model import (
    CanonicalRequest,
    ModelEvent,
    Role,
    TextBlock,
    Usage,
)
from pure_agent.tools import GlobTool, GrepTool, ReadFileTool, Sandbox, ToolRegistry, WriteFileTool


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── role / spec ────────────────────────────────────────────────────────


@pytest.mark.smoke
def test_list_roles() -> None:
    roles = list_roles()
    assert SubagentRole.GENERAL_PURPOSE in roles
    assert SubagentRole.EXPLORE in roles
    assert SubagentRole.PLAN in roles
    assert SubagentRole.VERIFY in roles


@pytest.mark.smoke
def test_get_spec() -> None:
    spec = get_spec(SubagentRole.EXPLORE)
    assert spec.read_only is True
    assert "write_file" not in spec.tools

    spec = get_spec(SubagentRole.GENERAL_PURPOSE)
    assert spec.read_only is False
    assert "write_file" in spec.tools


@pytest.mark.smoke
def test_build_request_uses_spec_defaults() -> None:
    req = build_request(SubagentRole.EXPLORE, "explore the project")
    assert req.role == SubagentRole.EXPLORE
    assert req.read_only is True
    assert "read_file" in req.tools_allow
    assert "write_file" not in req.tools_allow
    assert req.max_turns == 10


@pytest.mark.smoke
def test_build_request_overrides() -> None:
    req = build_request(
        SubagentRole.GENERAL_PURPOSE,
        "x",
        task_id="my-id",
        max_turns=3,
        tools_deny=["edit_file"],
    )
    assert req.task_id == "my-id"
    assert req.max_turns == 3
    assert "edit_file" in req.tools_deny


# ─── system prompt ──────────────────────────────────────────────────────


@pytest.mark.smoke
def test_make_subagent_system_prompt_read_only_warning() -> None:
    req = build_request(SubagentRole.EXPLORE, "x")
    sp = make_subagent_system_prompt(req)
    assert "exploration" in sp.lower() or "explore" in sp.lower()
    assert "READ-ONLY" in sp
    assert "NEVER" in sp or "Do NOT" in sp


@pytest.mark.smoke
def test_make_subagent_system_prompt_includes_context() -> None:
    req = build_request(
        SubagentRole.EXPLORE, "x", context={"hint": "look in src/"}
    )
    sp = make_subagent_system_prompt(req)
    assert "look in src/" in sp


# ─── registry filter ────────────────────────────────────────────────────


@pytest.mark.smoke
def test_filter_registry_allow(tmp_path) -> None:
    sandbox = Sandbox(root=tmp_path)
    reg = ToolRegistry()
    reg.register(ReadFileTool(sandbox))
    reg.register(WriteFileTool(sandbox))
    reg.register(GlobTool(sandbox))
    reg.register(GrepTool(sandbox))

    req = build_request(SubagentRole.EXPLORE, "x")
    new = filter_registry(reg, req)
    names = [t.name for t in new.all()]
    assert "read_file" in names
    assert "glob" in names
    assert "write_file" not in names
    assert "edit_file" not in names


@pytest.mark.smoke
def test_filter_registry_deny() -> None:
    sandbox = Sandbox(root=__import__("pathlib").Path("/tmp"))
    reg = ToolRegistry()
    reg.register(ReadFileTool(sandbox))
    reg.register(WriteFileTool(sandbox))
    req = SubagentRequest(
        task_id="t",
        role=SubagentRole.GENERAL_PURPOSE,
        prompt="x",
        tools_allow=["read_file", "write_file"],
        tools_deny=["write_file"],
    )
    new = filter_registry(reg, req)
    names = [t.name for t in new.all()]
    assert "read_file" in names
    assert "write_file" not in names


# ─── run_subagent with mock LLM ─────────────────────────────────────────


class MockProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        yield ModelEvent(type="message_start")
        yield ModelEvent(type="text_delta", text=self.text)
        yield ModelEvent(type="usage", usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        yield ModelEvent(type="message_end", finish_reason="stop")

    def normalize_tool_schema(self, s):
        return {}

    def max_context_tokens(self, m=None):
        return 128_000


def _make_loop_factory(provider):
    from pure_agent.agent import AIAgentLoop

    def factory(*, system_prompt: str = "", tools=None, max_turns: int = 10):
        return AIAgentLoop(
            provider=provider,
            tools=tools or ToolRegistry(),
            model="mock",
            system_prompt=system_prompt,
            max_turns=max_turns,
        )

    return factory


@pytest.mark.smoke
def test_run_subagent_explore() -> None:
    provider = MockProvider("found 5 files")
    req = build_request(SubagentRole.EXPLORE, "find python files", task_id="t1")
    registry = ToolRegistry()
    result = run(run_subagent(req, loop_factory=_make_loop_factory(provider), tool_registry=registry))
    assert isinstance(result, SubagentResponse)
    assert result.status == SubagentStatus.DONE
    assert result.summary == "found 5 files"
    assert result.usage.total_tokens == 15
    assert provider.calls == 1


@pytest.mark.smoke
def test_run_subagent_timeout() -> None:
    """A loop that never returns → subagent times out."""

    class SlowProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
            self.calls += 1
            await asyncio.sleep(10.0)
            yield ModelEvent(type="message_end")

        def normalize_tool_schema(self, s):
            return {}

        def max_context_tokens(self, m=None):
            return 128_000

    from pure_agent.agent import AIAgentLoop

    class SlowLoop(AIAgentLoop):
        def __init__(self, **kw):
            self.provider = SlowProvider()
            self.tools = kw.get("tools") or ToolRegistry()
            self.model = kw.get("model", "mock")
            self.max_turns = kw.get("max_turns", 10)
            self.system_prompt = kw.get("system_prompt", "")

        async def run(self, user_message, **kw):
            await asyncio.sleep(10.0)
            from pure_agent.model import AgentRunResult, StopReason
            return AgentRunResult(
                final_text="never", turns=0, total_usage=Usage(),
                stopped_reason=StopReason.COMPLETED, messages=[],
            )

    req = build_request(SubagentRole.EXPLORE, "x", timeout_s=0.1)
    result = run(run_subagent(req, loop_factory=lambda **kw: SlowLoop(**kw), tool_registry=ToolRegistry()))
    assert result.status == SubagentStatus.TIMEOUT
    assert "timed out" in (result.error or "")


@pytest.mark.smoke
def test_run_subagent_error_returns_failed() -> None:
    """A loop that raises → subagent status=FAILED."""

    class BrokenProvider:
        async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
            raise RuntimeError("boom")
            yield  # make it a generator

    from pure_agent.agent import AIAgentLoop

    class BrokenLoop(AIAgentLoop):
        def __init__(self, **kw):
            self.provider = BrokenProvider()
            self.tools = kw.get("tools") or ToolRegistry()
            self.model = kw.get("model", "mock")
            self.max_turns = kw.get("max_turns", 10)
            self.system_prompt = kw.get("system_prompt", "")

        async def run(self, user_message, **kw):
            raise RuntimeError("broken loop")

    req = build_request(SubagentRole.GENERAL_PURPOSE, "x", task_id="t2")
    result = run(run_subagent(req, loop_factory=lambda **kw: BrokenLoop(**kw), tool_registry=ToolRegistry()))
    assert result.status == SubagentStatus.FAILED
    assert "broken loop" in (result.error or "")
