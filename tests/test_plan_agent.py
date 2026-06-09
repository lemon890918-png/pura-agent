"""Tests for plan/agent.py — PlanAgent LLM decomposition with a MockProvider."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from pure_agent.model import (
    CanonicalRequest,
    ModelEvent,
    ProviderAdapter,
    Usage,
)
from pure_agent.plan import Goal, PlanAgent
from pure_agent.plan.agent import PlanAgent as PlanAgentCls


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── mock provider ─────────────────────────────────────────────────────────


class MockProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.last_request: CanonicalRequest | None = None

    async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]:
        self.last_request = request
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        text = self.responses[idx]
        yield ModelEvent(type="message_start")
        if text:
            yield ModelEvent(type="text_delta", text=text)
        yield ModelEvent(
            type="usage",
            usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
        yield ModelEvent(type="message_end", finish_reason="stop")

    def normalize_tool_schema(self, s):
        return {}

    def max_context_tokens(self, m=None):
        return 128_000


# ─── parse + validate ─────────────────────────────────────────────────────


@pytest.mark.smoke
def test_parse_fenced_json() -> None:
    text = """Here is the plan:
```json
{"steps": [{"kind": "read", "action": "Read foo.py", "deps": []}]}
```"""
    plan = PlanAgentCls._parse_and_validate(None, text, "g1")
    assert len(plan.steps) == 1
    assert plan.steps[0].id == "s1"
    assert plan.steps[0].kind.value == "read"


@pytest.mark.smoke
def test_parse_bare_json() -> None:
    # 2 steps with the 2nd depending on the 1st
    text = (
        '{"steps": ['
        '{"kind": "read", "action": "read x", "deps": []}, '
        '{"kind": "code", "action": "edit x", "deps": [1]}'
        ']}'
    )
    plan = PlanAgentCls._parse_and_validate(None, text, "g1")
    assert len(plan.steps) == 2
    assert plan.steps[1].deps == ["s1"]


@pytest.mark.smoke
def test_parse_multi_step_with_numeric_deps() -> None:
    text = """
```json
{
  "steps": [
    {"kind": "read", "action": "read foo", "deps": []},
    {"kind": "code", "action": "edit foo", "deps": [1]},
    {"kind": "verify", "action": "run tests", "deps": [1, 2]}
  ]
}
```"""
    plan = PlanAgentCls._parse_and_validate(None, text, "g1")
    assert len(plan.steps) == 3
    assert plan.steps[2].deps == ["s1", "s2"]


@pytest.mark.smoke
def test_parse_no_json_raises() -> None:
    with pytest.raises(ValueError, match="no JSON"):
        PlanAgentCls._parse_and_validate(None, "nothing useful", "g1")


@pytest.mark.smoke
def test_parse_unknown_kind_raises() -> None:
    text = '{"steps": [{"kind": "unknown_kind", "action": "x"}]}'
    with pytest.raises(ValueError, match="unknown step kind"):
        PlanAgentCls._parse_and_validate(None, text, "g1")


@pytest.mark.smoke
def test_parse_too_many_steps_raises() -> None:
    text = json.dumps(
        {
            "steps": [
                {"kind": "code", "action": f"step {i}", "deps": []}
                for i in range(25)
            ]
        }
    )
    with pytest.raises(ValueError, match="too many"):
        PlanAgentCls._parse_and_validate(None, text, "g1")


# ─── agent with mock LLM ──────────────────────────────────────────────────


@pytest.fixture
def sample_goal() -> Goal:
    return Goal(project_id="p1", text="build a thing")


@pytest.mark.smoke
def test_decompose_success(sample_goal) -> None:
    provider = MockProvider(
        [
            '```json\n{"steps": [{"kind": "read", "action": "explore", "deps": []}, '
            '{"kind": "code", "action": "implement", "deps": [1]}]}\n```'
        ]
    )
    agent = PlanAgent(provider, "mock")
    plan, usage = run(agent.decompose(sample_goal))
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "s1"
    assert plan.steps[1].deps == ["s1"]
    assert usage.total_tokens == 30


@pytest.mark.smoke
def test_decompose_retries_on_invalid_json(sample_goal) -> None:
    """First response is bad JSON, second is good."""
    provider = MockProvider(
        [
            "not json at all",
            '```json\n{"steps": [{"kind": "code", "action": "x", "deps": []}]}\n```',
        ]
    )
    agent = PlanAgent(provider, "mock")
    plan, usage = run(agent.decompose(sample_goal, max_attempts=3))
    assert len(plan.steps) == 1
    assert provider.calls == 2
    assert usage.total_tokens == 60  # 2 calls * 30


@pytest.mark.smoke
def test_decompose_gives_up_after_max_attempts(sample_goal) -> None:
    provider = MockProvider(["garbage"] * 5)
    agent = PlanAgent(provider, "mock")
    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        run(agent.decompose(sample_goal, max_attempts=3))
    assert provider.calls == 3
